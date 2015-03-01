#!/usr/bin/python2
#
#    Rules beets plugin
#
#    (c) 2015 Taeyeon Mori
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from __future__ import absolute_import, unicode_literals, division, print_function

from beets.ui import Subcommand as _Subcommand, show_model_changes
from beets.util import ancestry

import beets.plugins
import beets.ui
import beets.library
import beets.dbcore

import weakref
import collections
import shlex
import os


class ChangeSet(object):
    def __init__(self, mods, dels=[]):
        self.mods = mods
        self.dels = dels

    def apply_to(self, item):
        item.update(self.mods)

        for md_key in self.dels:
            if md_key in item:
                del item[md_key]

    def __repr__(self):
        return "ChangeSet(%s, %s)" % (self.mods, self.dels)


class Query(object):
    """
    Represents a (unsorted) Query.
    """
    def __init__(self, parts):
        # the query parts
        self.parts = parts

        self.compiled = {}

    def _compile(self, type=beets.library.Album):
        # @sa beets.library:parse_query_parts
        if type == "album":
            type = beets.library.Album
        elif type == "item":
            type = beets.library.Item

        # Get query types and their prefix characters.
        prefixes = {':': beets.dbcore.query.RegexpQuery}
        prefixes.update(beets.plugins.queries())

        # Special-case path-like queries, which are non-field queries
        # containing path separators (/).
        # Match field / flexattr depending on whether the model has the path field
        have_fast_path_query = 'path' in type._fields

        path_queries = []
        query_parts = []

        for s in self.parts:
            if s.find(os.sep, 0, s.find(':')) != -1:
                # Separator precedes colon.
                path_queries.append(PathQuery('path', s, have_fast_path_query))
            else:
                query_parts.append(s)

        query = beets.dbcore.query_from_strings(beets.dbcore.AndQuery, type, prefixes, query_parts)
        query.subqueries.extend(path_queries)

        self.compiled[type] = query

        return query

    def compile(self, type=beets.library.Album):
        if type not in self.compiled:
            self._compile(type)
        return self.compiled[type]

    def query(self, lib, type=beets.library.Album):
        """
        Query the Library for items
        """
        return lib._fetch(type, self.compile(type), None)

    def match(self, item, type=None):
        """
        Check if an item matches the query
        """
        return self.compile(item.__class__ if type is None else type).match(item) # FIXME avoid .__class__

    def __repr__(self):
        return "Query(%s)" % self.parts


class ModSpec(Query, ChangeSet):
    """
    Combines a Query with a Changeset into a complete modification operation.
    """
    def __init__(self, query, mods, dels=[], type=beets.library.Album):
        self.type = type

        Query.__init__(self, query)
        ChangeSet.__init__(self, mods, dels)

    @classmethod
    def parse_parts(cls, modspec):
        # See also: beets/ui/commands.py:modify_parse_args()
        mods = {}
        dels = []
        query = []
        type = beets.library.Album

        for entry in modspec:
            if entry[0] == '?':
                if entry == "?item":
                    type = beets.library.Item
                elif entry == "?album":
                    type = beets.library.Album
                else:
                    raise Exception("Known special tags: ?album, ?item")

            else:
                equals = entry.find('=')
                colon = entry.find(':')

                if equals == colon == -1 and entry[-1] == '!':
                    dels.append(entry[:-1])

                elif equals != -1 and (colon == -1 or colon > equals):
                    key, val = entry.split('=', 1)
                    mods[key] = val

                else:
                    query.append(entry)

        return cls(query, mods, dels, type)

    @classmethod
    def parse_string(cls, modspec):
        return cls.parse_parts(shlex.split(modspec))

    @classmethod
    def parse(cls, modspec):
        if isinstance(modspec, basestring):
            return cls.parse_string(modspec)
        else:
            return cls.parse_parts(modspec)

    def execute(self, lib):
        set = list(self.query(lib, self.type))

        for item in set:
            self.apply_to(item)

        return set

    def apply_match(self, item):
        """
        Checks for a match and applies the changeset
        """
        if self.match(item):
            self.apply_to(item)
            return True
        return False

    def __repr__(self):
        return "ModSpec(%s, %s, %s, %s)" % (
            self.parts, self.mods, self.dels,
            self.type.__name__
        )


class DirtySet(object):
    """
    A hack to keep at most one state per item around
    This is needed because retrieving items from the database creates new wrapper objects every time,
    with new dirty-maps. Therefore, to keep working on a fixed set, we need to keep that set around.

    The user is responsible for keeping objects alive!

    Please note that you may only use ONE EFFECTIVE LEAF CLASS PER TYPE
    so, if you use the beets.library.Album class for albums, DON'T MIX IT WITH A CUSTOM SUBCLASS
    """
    def __init__(self):
        self.dirty = collections.defaultdict(weakref.WeakValueDictionary)

    def __contains__(self, item):
        return item.id in self.dirty[type(item)]

    def catch(self, item):
        type_set = self.dirty[type(item)]
        if item.id in type_set:
            return type_set[item.id]
        else:
            type_set[item.id] = item
            return item

    def catch_iter(self, iter):
        for x in iter:
            yield self.catch(x)

    def get(self, type=beets.library.Album):
        return self.dirty[type].values()


class BatchLibrary(object):
    """
    Uses a DirtySet to provide a library that keeps track of the objects it hands out.
    This is used to accumulate changes across multiple queries.

    It should be possible to implement this without using a DirtySet, but that would require advanced knowledge about
    The library querying mechanism employed
    """
    def __init__(self, lib):
        self.library = lib
        self.dirty = DirtySet()

    def _fetch(self, type, query=None, sort=None):
        return self.dirty.catch_iter(self.library._fetch(type, query, sort))

    def items(self, query=None, sort=None):
        return self._fetch(beets.library.Item, query, sort)

    def albums(self, query=None, sort=None):
        return self._fetch(beets.library.Album, query, sort)


class Subcommand(_Subcommand):
    """
    Subcommand decorator that can be used on (class-)methods
    """
    class Bound(_Subcommand):
        def __init__(self, cmd, cmdself, cmdclass):
            self.cmd = cmd
            self.func = self.func.__get__(cmdself, cmdclass)

        def __getattr__(self, item):
            return getattr(self.cmd, item)

    def __call__(self, f):
        self.func = f
        return self

    def __get__(self, instance, owner):
        return self.Bound(self, instance, owner)


class RulesPlugin(beets.plugins.BeetsPlugin):
    def __init__(self):
        super(RulesPlugin, self).__init__()

        self._modspecs = None

        # Config
        self.show_changes = self.config["showchanges"]
        self.write = self.config["write"]
        self.move = self.config["move"]
        self.confirm = self.config["confirm"]
        self.on_import = self.config["onimport"]

        self.show_changes.add(True)
        self.write.add(True)
        self.move.add(True)
        self.confirm.add(True)
        self.on_import.add(False)

        if self.on_import.get(bool):
            self.import_stages.append(self.importer)

    def modspecs(self):
        if self._modspecs is None:
            from beets import config
            self._modspecs = [ModSpec.parse(ms) for ms in config["rules"].get(list)]
        return self._modspecs

    @Subcommand("rules-apply", help="Apply configured beets-rules")
    def apply_command(self, lib, opts, args):
        lib = BatchLibrary(lib)

        modified = set()
        for modspec in self.modspecs():
            diff = modspec.execute(lib)
            modified.update(diff)

        if not modified:
            print("No changes to make.")
            return

        # Config
        show_changes = self.show_changes.get(bool)
        write = self.write.get(bool)
        move = self.move.get(bool)
        confirm = self.confirm.get(bool)

        # print changes
        if show_changes:
            for item in modified:
                show_model_changes(item)

            if write and move:
                extra = ', move and write tags'
            elif write:
                extra = ' and write tags'
            elif move:
                extra = ' and move'
            else:
                extra = ''

            if confirm and not beets.ui.input_yn('Really modify%s (Y/n)?' % extra):
                return

        # Apply changes to database and files
        with lib.transaction():
            for obj in modified:
                if move:
                    cur_path = obj.path
                    if lib.directory in ancestry(cur_path):  # In library?
                        log.debug(u'moving object {0}', displayable_path(cur_path))
                        obj.move()

                obj.try_sync(write)

        return

    @Subcommand("rules-test")
    def test_command(self, lib, opts, args):
        import pprint
        pprint.pprint([x for x in self.modspecs()])

    def commands(self):
        return [self.apply_command, self.test_command]

    def importer(self, session, task):
        for item in task.imported_items():
            for modspec in self.modspecs():
                if modspec.match(item):
                    modspec.apply_to(item)
                    item.try_sync()
