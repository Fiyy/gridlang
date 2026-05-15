"""Tests for gridlang.charts"""

import pytest
import pandas as pd
from gridlang.charts import (
    sparkline, bar_chart, line_chart, pie_chart,
    scatter_chart, area_chart, stacked_bar_chart,
    heatmap, color_scale, get_all_charts,
)


class TestSparkline:
    def test_basic(self):
        svg = sparkline([1, 3, 2, 5, 4])
        assert "<svg" in svg
        assert "polyline" in svg

    def test_empty(self):
        assert sparkline([]) == ""
        assert sparkline([5]) == ""  # Need at least 2 points


class TestBarChart:
    def test_basic(self):
        svg = bar_chart(['A', 'B', 'C'], [10, 20, 30])
        assert "<svg" in svg
        assert "rect" in svg
        assert "A" in svg

    def test_empty(self):
        assert bar_chart([], []) == ""

    def test_with_title(self):
        svg = bar_chart(['X'], [5], title="Test Chart")
        assert "Test Chart" in svg


class TestLineChart:
    def test_single_series(self):
        svg = line_chart(['Jan', 'Feb', 'Mar'], [10, 20, 15])
        assert "<svg" in svg
        assert "polyline" in svg

    def test_multi_series(self):
        svg = line_chart(
            ['Q1', 'Q2', 'Q3'],
            {'Sales': [10, 20, 30], 'Costs': [8, 15, 22]}
        )
        assert "<svg" in svg
        assert "Sales" in svg
        assert "Costs" in svg

    def test_empty(self):
        assert line_chart([], []) == ""


class TestPieChart:
    def test_basic(self):
        svg = pie_chart(['A', 'B', 'C'], [30, 50, 20])
        assert "<svg" in svg
        assert "path" in svg
        assert "Total" in svg

    def test_empty(self):
        assert pie_chart([], []) == ""

    def test_zero_total(self):
        assert pie_chart(['X'], [0]) == ""


class TestScatterChart:
    def test_basic(self):
        svg = scatter_chart([1, 2, 3, 4], [10, 20, 15, 25])
        assert "<svg" in svg
        assert "circle" in svg

    def test_mismatched_lengths(self):
        assert scatter_chart([1, 2], [1]) == ""

    def test_with_labels(self):
        svg = scatter_chart([1, 2], [3, 4], x_label="X", y_label="Y")
        assert "X" in svg
        assert "Y" in svg


class TestAreaChart:
    def test_single_series(self):
        svg = area_chart(['A', 'B', 'C'], [10, 20, 15])
        assert "<svg" in svg
        assert "path" in svg

    def test_multi_series(self):
        svg = area_chart(
            ['A', 'B', 'C'],
            {'Revenue': [10, 20, 30], 'Cost': [5, 10, 15]}
        )
        assert "<svg" in svg


class TestStackedBarChart:
    def test_basic(self):
        svg = stacked_bar_chart(
            ['Q1', 'Q2', 'Q3'],
            {'Sales': [10, 20, 30], 'Support': [5, 8, 12]}
        )
        assert "<svg" in svg
        assert "rect" in svg
        assert "Sales" in svg

    def test_empty(self):
        assert stacked_bar_chart([], {}) == ""


class TestHeatmap:
    def test_basic(self):
        df = pd.DataFrame({
            'Label': ['A', 'B', 'C'],
            'Col1': [10, 50, 90],
            'Col2': [20, 60, 80],
        })
        svg = heatmap(df)
        assert "<svg" in svg
        assert "rect" in svg

    def test_no_numeric(self):
        df = pd.DataFrame({'A': ['x', 'y'], 'B': ['a', 'b']})
        assert heatmap(df) == ""


class TestColorScale:
    def test_basic(self):
        result = color_scale(50, 0, 100)
        assert result.startswith('#')
        assert len(result) == 7

    def test_extremes(self):
        low = color_scale(0, 0, 100)
        high = color_scale(100, 0, 100)
        assert low != high

    def test_invalid(self):
        result = color_scale("not a number")
        assert "#" in result


class TestGetAllCharts:
    def test_returns_all(self):
        charts = get_all_charts()
        assert len(charts) == 9
        assert 'sparkline' in charts
        assert 'pie_chart' in charts
        assert 'heatmap' in charts
