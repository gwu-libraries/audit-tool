from unittest import TestCase
import tempfile
import shutil
from inventory import Inventory
from inventory_manager import InventoryManager
import os
import hashlib
import logging

log = logging.getLogger(__name__)


class TestInventory(TestCase):
    def setUp(self):
        self.base_path = tempfile.mkdtemp()
        self.inventory_base_path = tempfile.mkdtemp()
        self.inventory_manager = InventoryManager(self.base_path, self.inventory_base_path)

    def tearDown(self):
        shutil.rmtree(self.base_path, ignore_errors=True)
        shutil.rmtree(self.inventory_base_path, ignore_errors=True)

    def create_test_data(self):
        path = tempfile.mkdtemp(dir=self.base_path)
        self.create_test_file(os.path.join(path, 'file1.txt'))
        self.create_test_file(os.path.join(path, 'file2.txt'))
        dir1_path = os.path.join(path, 'dir1')
        os.mkdir(dir1_path)
        self.create_test_file(os.path.join(dir1_path, 'file3.txt'))
        dir2_path = os.path.join(dir1_path, 'dir2')
        os.mkdir(dir2_path)
        self.create_test_file(os.path.join(dir2_path, 'file4.txt'))
        os.mkdir(os.path.join(path, 'dir3'))
        return path

    @staticmethod
    def create_test_file(filepath, contents=None):
        with open(filepath, mode='w') as f:
            f.write(contents or os.path.basename(filepath))

    def test_detect_changes_and_update(self):
        fs_path = self.create_test_data()
        inventories = Inventory.perform_recursive_inventory(os.path.dirname(fs_path), self.base_path)
        for inventory in inventories:
            inventory.write(self.inventory_base_path)

        # Nothing changes
        self.assertEqual([], self.inventory_manager.detect_change(fs_path).inventory_diffs)

        # Add a directory and new file
        dir4_path = os.path.join(fs_path, 'dir4')
        os.mkdir(dir4_path)
        self.create_test_file(os.path.join(dir4_path, 'file5.txt'))
        # Remove a directory
        shutil.rmtree(os.path.join(fs_path, 'dir3'))
        # Add a file
        self.create_test_file(os.path.join(fs_path, 'file6.txt'))
        # Remove a file
        os.remove(os.path.join(fs_path, 'file1.txt'))
        # Change a file
        self.create_test_file(os.path.join(fs_path, 'dir1/file3.txt'), contents='test')

        inventory_report = self.inventory_manager.detect_change(fs_path)
        rel_path = os.path.relpath(fs_path, self.base_path)
        self.assertInventoryDiffEqual({
            'path': rel_path,
            'directories_missing_from_fs': ['dir3'],
            'directories_missing_from_inventory': ['dir4'],
            'files_missing_from_fs': {'file1.txt': hashlib.sha256('file1.txt'.encode('utf-8')).hexdigest()},
            'files_missing_from_inventory': {'file6.txt': hashlib.sha256('file6.txt'.encode('utf-8')).hexdigest()},
            'file_fixity_mismatch': {}
        }, self._find_inventory_diff(inventory_report.inventory_diffs, rel_path))

        dir4_rel_path = os.path.join(rel_path, 'dir4')
        self.assertInventoryDiffEqual({
            'path': dir4_rel_path,
            'directories_missing_from_fs': [],
            'directories_missing_from_inventory': [],
            'files_missing_from_fs': {},
            'files_missing_from_inventory': {'file5.txt': hashlib.sha256('file5.txt'.encode('utf-8')).hexdigest()},
            'file_fixity_mismatch': {}
        }, self._find_inventory_diff(inventory_report.inventory_diffs, dir4_rel_path))

        dir1_rel_path = os.path.join(rel_path, 'dir1')
        self.assertInventoryDiffEqual({
            'path': dir1_rel_path,
            'directories_missing_from_fs': [],
            'directories_missing_from_inventory': [],
            'files_missing_from_fs': {},
            'files_missing_from_inventory': {},
            'file_fixity_mismatch': {'file3.txt': (hashlib.sha256('file3.txt'.encode('utf-8')).hexdigest(),
                                                   hashlib.sha256('test'.encode('utf-8')).hexdigest())}
        }, self._find_inventory_diff(inventory_report.inventory_diffs, dir1_rel_path))

        self.inventory_manager.update_inventory(inventory_report)
        # Nothing changes
        self.assertEqual([], self.inventory_manager.detect_change(fs_path).inventory_diffs)

    @staticmethod
    def _find_inventory_diff(inventory_diffs, path):
        for inventory_diff in inventory_diffs:
            if inventory_diff.path == path:
                return inventory_diff
        return None

    def assertInventoryDiffEqual(self, inventory_diff1_dict, inventory_diff2):
        inventory_diff2_dict = inventory_diff2.as_dict()
        if 'timestamp' in inventory_diff2_dict:
            del inventory_diff2_dict['timestamp']
        self.assertEqual(inventory_diff1_dict, inventory_diff2_dict)
