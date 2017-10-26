from unittest import TestCase
import tempfile
import shutil
from inventory import Inventory
import os
import hashlib
import logging

log = logging.getLogger(__name__)


class TestInventory(TestCase):
    def setUp(self):
        self.fs_base_path = tempfile.mkdtemp()
        self.inventory_base_path = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.fs_base_path, ignore_errors=True)
        shutil.rmtree(self.inventory_base_path, ignore_errors=True)

    def create_test_data(self):
        path = tempfile.mkdtemp(dir=self.fs_base_path)
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
    def create_test_file(filepath):
        with open(filepath, mode='w') as f:
            f.write(os.path.basename(filepath))

    def test_perform_inventory(self):
        path = self.create_test_data()
        inventory = Inventory.perform_inventory(path, self.fs_base_path)
        self.assertTrue(path.endswith(inventory.path))
        self.assertEqual({'dir1', 'dir3'}, inventory.dirs)
        self.assertEqual({
            'file2.txt': hashlib.sha256('file2.txt'.encode('utf-8')).hexdigest(),
            'file1.txt': hashlib.sha256('file1.txt'.encode('utf-8')).hexdigest()
        }, inventory.files)

    def test_perform_recursive(self):
        path = self.create_test_data()
        inventories = Inventory.perform_recursive_inventory(path, self.fs_base_path)
        self.assertEqual(4, len(inventories))

        inventory1 = self._find_inventory(inventories, os.path.basename(path))
        self.assertEqual({'dir1', 'dir3'}, inventory1.dirs)
        self.assertEqual(2, len(inventory1.files))

        inventory2 = self._find_inventory(inventories, os.path.join(inventory1.path, 'dir1'))
        self.assertEqual({'dir2'}, inventory2.dirs)
        self.assertEqual({
            'file3.txt': hashlib.sha256('file3.txt'.encode('utf-8')).hexdigest()
        }, inventory2.files)

        inventory3 = self._find_inventory(inventories, os.path.join(inventory2.path, 'dir2'))
        self.assertEqual(set(), inventory3.dirs)
        self.assertEqual({
            'file4.txt': hashlib.sha256('file4.txt'.encode('utf-8')).hexdigest()
        }, inventory3.files)

        inventory4 = self._find_inventory(inventories, os.path.join(inventory1.path, 'dir3'))
        self.assertEqual(set(), inventory4.dirs)
        self.assertEqual({}, inventory4.files)

    @staticmethod
    def _find_inventory(inventories, path):
        for inventory in inventories:
            if inventory.path == path:
                return inventory
        return None

    def test_inventory_filepath(self):
        inventory = Inventory('test')
        self.assertEqual('9f86d081/884c7d65/9a2feaa0/c55ad015/a3bf4f1b/2b0b822c/d15d6c15/b0f00a08/9f86d081884c7d659a2f'
                         'eaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08.json', inventory.inventory_filepath)

    def test_write_read(self):
        path = self.create_test_data()
        inventory1 = Inventory.perform_inventory(path, self.fs_base_path)
        inventory_filepath = os.path.join(self.inventory_base_path, inventory1.inventory_filepath)
        self.assertFalse(os.path.exists(inventory_filepath))
        inventory1.write(self.inventory_base_path)
        self.assertTrue(os.path.exists(inventory_filepath))

        inventory2 = Inventory.read(inventory1.path, self.inventory_base_path)
        self.assertInventoryEqual(inventory1, inventory2)

    def test_diff_and_update(self):
        inventory1 = Inventory('test', dirs=['dir1', 'dir2'],
                               files={'file1.txt': '12345', 'file2.txt': '23456', 'file3.txt': '34567'})
        inventory2 = Inventory('test', dirs=['dir1', 'dir3'],
                               files={'file1.txt': '12345', 'file2.txt': 'x23456', 'file4.txt': '45678'})
        self.assertInventoryNotEqual(inventory1, inventory2)
        (directories_missing_from_this, directories_missing_from_that, files_missing_from_this,
         files_missing_from_that, file_fixity_mismatch) = inventory1.diff(inventory2)
        self.assertEqual({'dir3'}, directories_missing_from_this)
        self.assertEqual({'dir2'}, directories_missing_from_that)
        self.assertEqual({'file4.txt': '45678'}, files_missing_from_this)
        self.assertEqual({'file3.txt': '34567'}, files_missing_from_that)
        self.assertEqual({'file2.txt': ('23456', 'x23456')}, file_fixity_mismatch)

        inventory1.update(directories_missing_from_this, directories_missing_from_that, files_missing_from_this,
                          files_missing_from_that, file_fixity_mismatch)
        self.assertInventoryEqual(inventory1, inventory2)

    def assertInventoryEqual(self, inventory1, inventory2):
        inventory1_dict = inventory1.as_dict()
        del inventory1_dict['timestamp']
        inventory2_dict = inventory2.as_dict()
        del inventory2_dict['timestamp']
        self.assertEqual(inventory1_dict, inventory2_dict)

    def assertInventoryNotEqual(self, inventory1, inventory2):
        inventory1_dict = inventory1.as_dict()
        del inventory1_dict['timestamp']
        inventory2_dict = inventory2.as_dict()
        del inventory2_dict['timestamp']
        self.assertNotEqual(inventory1_dict, inventory2_dict)
