import unittest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

class TestImports(unittest.TestCase):
    def test_imports(self):
        try:
            import hash_generator
            import duplicate_detector
            import folder_scanner
            self.assertTrue(True)
        except ImportError:
            self.fail("Failed to import modules")

class TestBasicFunctionality(unittest.TestCase):
    def test_bk_tree_init(self):
        from duplicate_detector import BKTree
        tree = BKTree()
        self.assertIsNotNone(tree)

if __name__ == '__main__':
    unittest.main()