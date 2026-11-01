"""影视库海报网格的列数与分页数量的整除约束。

海报网格使用固定的列数（6/4/3/2，按断点切换）而不是 `auto-fill`：
只有列数能整除「每页」选项时，整页数据才会排成整齐的整行，
不会出现末行只剩一张海报的情况。这里把该约束固化成回归测试。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "frontend" / "src" / "components" / "Re0PosterGrid.jsx"
STYLES = ROOT / "frontend" / "src" / "styles.css"


def page_size_options():
    source = COMPONENT.read_text(encoding="utf-8")
    return sorted({int(value) for value in re.findall(r"<option value=\{(\d+)\}>", source)})


def grid_column_counts():
    source = STYLES.read_text(encoding="utf-8")
    pattern = r"\.re0-poster-grid\s*\{[^}]*?grid-template-columns:\s*repeat\((\d+)"
    return sorted({int(count) for count in re.findall(pattern, source)})


class PosterGridLayoutTest(unittest.TestCase):
    def test_page_size_options_exist(self):
        self.assertTrue(page_size_options(), "Re0PosterGrid 的每页选项未找到")

    def test_grid_uses_fixed_columns(self):
        columns = grid_column_counts()
        self.assertTrue(columns, "海报网格应使用固定列数（repeat(N, ...)），不能用 auto-fill")
        self.assertTrue(all(column >= 2 for column in columns), columns)

    def test_every_page_size_fills_whole_rows(self):
        page_sizes = page_size_options()
        columns = grid_column_counts()
        for page_size in page_sizes:
            for column in columns:
                with self.subTest(page_size=page_size, columns=column):
                    self.assertEqual(
                        page_size % column,
                        0,
                        f"每页 {page_size} 条在 {column} 列时末行只剩 {page_size % column} 张海报",
                    )


if __name__ == "__main__":
    unittest.main()
