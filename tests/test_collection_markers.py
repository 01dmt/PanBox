from __future__ import annotations

import unittest

from backend.collection_markers import COLLECTION_RE, has_collection_marker, strip_collection_marker


class CollectionMarkerTests(unittest.TestCase):
    def test_chinese_collection_variants_are_recognized(self) -> None:
        for value in (
            "生化危机（系列）",
            "成龙电影合集",
            "周星驰合辑",
            "周星馳合輯",
            "漫威套装",
            "漫威套裝",
            "漫威 套装",
            "指环王套盒",
            "火影合集包",
            "火影忍者全系列绝版大合集",
            "法证先锋全系列1-6",
            "燕双鹰前三部曲",
            "龙游天下三部曲",
            "指环王 全3部 加长版",
            "生化危机法版4k原盘remux全六部",
            "疾速追杀5部",
            "哆啦A梦剧场版 21部国语版本",
            "53部港台剧",
            "生化危机1~6部]",
            "神奇四侠 2005-2007-2015三部 Remux 英语",
            "招魂系列",
        ):
            with self.subTest(value=value):
                self.assertTrue(has_collection_marker(value))

    def test_real_titles_are_not_collections(self) -> None:
        for value in (
            "诛仙合集篇",
            "性梦爱三部曲性",
            "性梦爱三部曲梦",
            "兰香如故（全集）",
            "套装",
            "蓝白红三部曲之红",
            "蓝白红三部曲之蓝",
            "天龙八部",
            "封神第一部：朝歌风云",
            "一部塞尔维亚电影",
            "九部的检察官",
            "音乐 古典交响 SACD 第三部分 ed2k",
            "Threesome Anthology",
            "The Collection",
        ):
            with self.subTest(value=value):
                self.assertFalse(COLLECTION_RE.search(value))

    def test_bracketed_english_collection_is_recognized(self) -> None:
        self.assertTrue(has_collection_marker("Marvel (Collection)"))
        self.assertTrue(has_collection_marker("Marvel Box Set"))

    def test_only_bracketed_series_marker_is_stripped(self) -> None:
        self.assertEqual(strip_collection_marker("生化危机（系列）"), "生化危机")
        self.assertEqual(strip_collection_marker("兰香如故（全集）"), "兰香如故")
        # A suffix marker is removed by the heading parser, not by this helper.
        self.assertEqual(strip_collection_marker("成龙电影合集"), "成龙电影合集")


if __name__ == "__main__":
    unittest.main()
