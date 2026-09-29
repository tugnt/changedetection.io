#!/usr/bin/env python3

import unittest

from changedetectionio.store import ChangeDetectionStore
from changedetectionio.blueprint.watchlist.filters import watch_matches_tag


class TestTagHierarchy(unittest.TestCase):
    def setUp(self):
        self.store = ChangeDetectionStore.__new__(ChangeDetectionStore)
        self.store._ChangeDetectionStore__data = {
            'settings': {
                'application': {
                    'tags': {
                        'camera': {'title': 'Camera', 'parent_uuid': None},
                        'film': {'title': 'Film camera', 'parent_uuid': 'camera'},
                        'digital': {'title': 'Digital camera', 'parent_uuid': 'camera'},
                        'compact': {'title': 'Compact camera', 'parent_uuid': 'digital'},
                    }
                }
            }
        }

    def test_descendants_include_nested_children(self):
        self.assertEqual(
            self.store.get_tag_descendant_uuids('camera'),
            {'camera', 'film', 'digital', 'compact'},
        )
        self.assertEqual(
            self.store.get_tag_descendant_uuids('digital'),
            {'digital', 'compact'},
        )

    def test_ancestors_include_parent_chain(self):
        self.assertEqual(
            self.store.get_tag_ancestor_uuids('compact'),
            {'compact', 'digital', 'camera'},
        )

    def test_tree_rows_are_parent_before_child(self):
        rows = self.store.get_tag_tree_rows()
        self.assertEqual(
            [(uuid, depth) for uuid, _, depth in rows],
            [('camera', 0), ('digital', 1), ('compact', 2), ('film', 1)],
        )

    def test_parent_validation_rejects_missing_and_cycles(self):
        self.assertTrue(self.store.validate_tag_parent('film', 'digital'))
        self.assertFalse(self.store.validate_tag_parent('camera', 'compact'))
        self.assertFalse(self.store.validate_tag_parent('film', 'missing'))
        self.assertTrue(self.store.validate_tag_parent('camera', None))

    def test_tree_rows_remain_safe_with_a_cycle(self):
        self.store._ChangeDetectionStore__data['settings']['application']['tags']['camera']['parent_uuid'] = 'compact'
        rows = self.store.get_tag_tree_rows()
        self.assertEqual({uuid for uuid, _, _ in rows}, {'camera', 'film', 'digital', 'compact'})

    def test_parent_filter_matches_child_but_not_sibling(self):
        class WatchStore:
            def __init__(self, tag_uuids):
                self.tag_uuids = tag_uuids

            def get_all_tags_for_watch(self, uuid):
                return {tag_uuid: {} for tag_uuid in self.tag_uuids}

        parent_filter = {
            'tag_uuid': 'camera',
            'tag_uuids': {'camera', 'film', 'digital', 'compact'},
        }
        self.assertTrue(watch_matches_tag(WatchStore({'film'}), {'uuid': 'child'}, parent_filter))
        self.assertFalse(watch_matches_tag(WatchStore({'other'}), {'uuid': 'sibling'}, parent_filter))


if __name__ == '__main__':
    unittest.main()
