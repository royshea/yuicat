#!/usr/bin/python
#
# Copyright 2010 Parkipedia (Dan Hipschman, Roy Shea)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""Prepare HTML, CSS, and JavaScript files for distribution."""

# TODO: Add '--clean' option that traverses the include_dict to remove
# all CSS and JavaScript files used after concatenation and compression
# is done.
#
# TODO: Only process logically rooted CSS and JS files.

from __future__ import with_statement

from glob import glob
from optparse import OptionParser
import os
import re
import shutil
import stat
from subprocess import PIPE, Popen
import sys

import yaml

# The yuicompressor jar.
#
# NOTE: Value of YUI_JAR is set in main after processing command line
# options.
YUI_JAR = None

# Assumed directory structure for a project.
#
# NOTE: Value of LAYOUT is set in main after processing command line
# options.
LAYOUT = None
LAYOUT_YAML = """
css:
    physical: static/stylesheets
    logical: /stylesheets
js:
    physical: static/js
    logical: /js
html: templates
"""

VALID_TYPES = ['css', 'js']

# Expected format of script and link tags.
#
# TODO: Make this more flexible so that tags with properties in
# different orders are still recognized.
element_template = {}
element_template['js'] = '<script type="text/javascript" src="%s"></script>\n'
element_template['css'] = '<link rel="stylesheet" href="%s">\n'

regex = {}
regex['js'] = re.compile(element_template['js'] % r'(/[^"]+\.js)')
regex['css'] = re.compile(element_template['css'] % (r'(/[^"]+\.css)',))
html_comment = re.compile(r'\s*<!--.*-->\s*')


def _error(msg, *args):
    print >>sys.stderr, msg % args
    sys.exit(1)


def yuicompress_files(files, out_name, file_type):
    """Process a set of files using the yuicompressor."""
    # Start up the yuicompressor.
    proc = Popen(['java', '-jar', YUI_JAR, '--type', file_type, '-o',
                 out_name], stdin=PIPE)
    # Feed files to the yuicompressor.
    for file_name in files:
        with open(file_name, 'r') as f:
            proc.stdin.write(f.read())
    proc.stdin.close()
    # Let yuicompressor do its magic.
    retval = proc.wait()
    if retval != 0:
        _error('yuicompressor failed with exit status %s', retval)


def _logical_to_physical(file_name, file_type):
    """Return physical location of logically rooted file_name."""
    (dir_name, base_name) = os.path.split(file_name)
    layout = LAYOUT[file_type]
    if not os.path.commonprefix([layout['logical'], dir_name]) == layout['logical']:
        _error("Not a logically rooted file: %s", file_name)
    return os.path.join(layout['physical'], base_name)


def _physical_to_logical(file_name, file_type):
    """Return logical location of physically rooted file_name."""
    (dir_name, base_name) = os.path.split(file_name)
    layout = LAYOUT[file_type]
    if not os.path.commonprefix([layout['physical'], dir_name]) == layout['physical']:
        _error("Not a physically rooted file: %s", file_name)
    return os.path.join(layout['logical'], base_name)


def patch_html(file_type, html_file, prefix, no_backup=False):
    """Merge all includes of specified type within html_file."""
    # Tack if the parser has encountered a non-script tag after seeing a
    # script tag.
    #
    # NOTE: This is used to force all script tags in an HTML document
    # into a single block.
    past_script = False

    # Read in the file to patch and set all indexes to zero.
    with open(html_file, 'r') as f:
        lines = f.readlines()
    line_num = 0
    last_match = 0

    # Text of the HTML element that will be added to the HTML file.
    base_name = '%s_%s.%s' % (prefix,
                              os.path.splitext(os.path.basename(html_file))[0],
                              file_type)
    logical_name = os.path.join(LAYOUT[file_type]['logical'], base_name)
    html_element = element_template[file_type] % (logical_name,)

    # Iterate through lines of the files looking for includes of the
    # specified type.  Any such includes found are removed from the file
    # and will be replaced with by including a single file containing
    # all includes in a minimized form.
    includes = []
    while line_num < len(lines):
        if lines[line_num] == html_element:
            _error("File and type already processed by yuicat: ",
                   html_file, file_type)
        # Ignore blank lines and comments.
        if lines[line_num] == '\n' or html_comment.match(lines[line_num]):
            line_num += 1
            continue
        match = regex[file_type].search(lines[line_num])
        if match:
            file_name = match.group(1)
            file_name = _logical_to_physical(file_name, file_type)
            del lines[line_num]
            last_match = line_num
            if past_script and file_type == 'js':
                _error('%s:%s: script found after non-script stuff; '
                       'cannot concatenate', html_file, line_num + 1)
            if file_name in includes:
                _error('%s:%s: %s included more than once.',
                       html_file, line_num + 1, file_name)
            includes.append(file_name)
        else:
            line_num += 1
            past_script = bool(includes)

    # Process included files and update HTML to use the newly created
    # resource.
    if includes:
        out_file = os.path.join(LAYOUT[file_type]['physical'], base_name)
        yuicompress_files(includes, out_file, file_type)
        lines.insert(last_match, html_element)
        stinfo = os.stat(html_file)
        (atime, mtime) = stinfo[stat.ST_ATIME], stinfo[stat.ST_MTIME]
        if not no_backup:
            # TODO: Clean up creation of backup files.  Perhaps use time
            # stamps?
            shutil.copy(html_file, '%s.%s.bak' % (html_file, file_type))
        with open(html_file, 'w') as f:
            f.writelines(lines)
        os.utime(html_file, (atime, mtime))
    return includes


def main(argv=None):
    """Configure yuicat tool and then process all found HTML."""

    if argv is None:
        argv = sys.argv[1:]
    usage = "Usage: %prog [OPTIONS] TYPE"
    epilog = ("Prepares HTML files for release by concatenating and "
              "compressing local JavaScript or CSS files used by "
              "individual HTML files.  NOTE: This tool modifies "
              "the underlying HTML files.  Most users will want to "
              "run this tool on a copy of the website."
              )
    parser = OptionParser(usage=usage, epilog=epilog)
    parser.add_option(
        '-t', '--type',
        metavar='TYPE',
        default='css,js',
        help="Comma separated list of file types to concatenate and "
             "compress.  Valid types are css and js. [default: %default]"
        )
    parser.add_option(
        '-p', '--prefix',
        metavar='PREFIX',
        default='yuicat',
        help="Prefix to prepend to generated files. [default: %default]"
        )
    parser.add_option(
        '-o', '--outfile',
        metavar='FILE',
        help="Record in FILE the files concatenated and compressed "
             "from each HTML file."
        )
    parser.add_option(
        '-n', '--no-backup',
        default=False,
        action='store_true',
        help="Disable backing up of the HTML files before modifying them."
        )
    parser.add_option(
        '-l', '--layout',
        metavar='FILE',
        help="File specifying physical and logical directory layout."
        )
    parser.add_option(
        '--yuijar',
        metavar='JAR',
        default='yuicompressor-2.4.2.jar',
        help="Path to yuicompressor. [default: %default]"
        )
    (options, args) = parser.parse_args(argv)
    if len(args) != 0:
        parser.error("Unexpected arguments: %s" % str(args))
    global YUI_JAR
    YUI_JAR = options.yuijar
    global LAYOUT
    if options.layout:
        with open(options.layout) as f:
            LAYOUT = yaml.load(f.read())
    else:
        LAYOUT = yaml.load(LAYOUT_YAML)

    # Update each HTML file.
    include_dict = {}
    for file_type in options.type.split(','):
        if file_type not in VALID_TYPES:
            _error("Invalid type: ", file_type)
        for html_file in glob(os.path.join(LAYOUT['html'], '*.html')):
            includes = patch_html(file_type, html_file, options.prefix,
                                  options.no_backup)
            typed_includes = include_dict.setdefault(file_type, {})
            typed_includes[html_file] = includes

    # Track files included by each HTML file.  Data is stored in a
    # nested dictionary indexed by file_type and then base file name:
    # - include_dict[file_type][html_file]
    #
    # TODO: Revise this to load an old outfile and extend it using new
    # data from this run of yuicat.
    #
    # TODO: Add a consistency check that verifies that includes always
    # occur in the same order across multiple files.
    if options.outfile:
        with open(options.outfile, 'w') as f:
            f.write(yaml.dump(include_dict))


if __name__ == '__main__':
    main()
