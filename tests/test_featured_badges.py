"""Publication cleanup keeps live badges and removes retired gallery files."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from site_files import reconcile_generated_pages


class FeaturedBadgeCleanupTests(unittest.TestCase):
    names = ('index.html', 'featured-by-awesomecua-laurel.svg', 'featured-by-awesomecua.svg',
             'featured-by-awesomecua-dark.svg', 'awesomecua-featured-seal.svg')

    def test_live_gallery_preserves_badges(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'badges'
            folder.mkdir()
            for name in self.names:
                (folder / name).write_text(name, encoding='utf-8')
            reconcile_generated_pages(root, [{'path': '/badges/'}])
            self.assertEqual({p.name for p in folder.iterdir()}, set(self.names))

    def test_retired_gallery_cleans_orphans_but_preserves_live_count_badge(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'badges'
            folder.mkdir()
            for name in (*self.names, 'catalog.svg', 'notes.txt'):
                (folder / name).write_text(name, encoding='utf-8')
            reconcile_generated_pages(root, [])
            self.assertEqual({p.name for p in folder.iterdir()}, {'catalog.svg', 'notes.txt'})


if __name__ == '__main__':
    unittest.main()
