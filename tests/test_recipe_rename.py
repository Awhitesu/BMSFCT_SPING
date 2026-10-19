import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from data.db_manager import DBManager


class RecipeRenameTests(unittest.TestCase):
    def test_rename_preserves_steps_and_rejects_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            db = DBManager(str(Path(folder)/'test.db'))
            with closing(db),patch.object(db, 'get_recipe_dir', return_value=folder):
                original = {'name': '旧配方', 'items': [{'name': '下载', 'params': 'PROJECT:a.opj'}]}
                self.assertTrue(db.save_recipe_json('旧配方', original))
                self.assertTrue(db.rename_recipe('旧配方', '新配方'))
                self.assertIsNone(db.load_recipe_json('旧配方'))
                data = db.load_recipe_json('新配方')
                self.assertEqual(data['name'], '新配方')
                self.assertEqual(data['items'], original['items'])
                db.save_recipe_json('其他配方', {'name': '其他配方'})
                self.assertFalse(db.rename_recipe('新配方', '其他配方'))
                self.assertFalse(db.rename_recipe('新配方', '../越界'))
                self.assertFalse(db.rename_recipe('新配方', 'CON'))
                self.assertEqual(db.load_recipe_json('新配方'), data)


if __name__ == '__main__':
    unittest.main()
