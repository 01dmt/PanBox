import unittest
from backend.recognition import parse
from backend.importers import parse_ed2k_link

class RecognitionTests(unittest.TestCase):
    def test_common_non_sxe_episode_forms(self):
        self.assertEqual((parse('Show.2024.1x02.mkv').season, parse('Show.2024.1x02.mkv').episode), (1, 2))
        result = parse('Show.2024.Episode-03.mkv')
        self.assertEqual((result.season, result.episode), (1, 3))
        self.assertEqual(parse('某剧.第三集.mkv').episode, 3)

    def test_directory_context_wins(self):
        result = parse('S01E02.1080p.mkv', main_dir_name='The.Show (2024)')
        self.assertEqual(result.title, 'The Show')
        self.assertEqual((result.year, result.media_type), (2024, 'tv'))

    def test_ed2k_uses_fallback_recognizer(self):
        source = parse_ed2k_link('ed2k://|file|Show.2024.1x02.1080p.mkv|100|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/')
        self.assertEqual((source.season, source.episode, source.media_type), (1, 2, 'tv'))

if __name__ == '__main__':
    unittest.main()
