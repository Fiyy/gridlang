"""Tests for gridlang.formulas"""

import pytest
import pandas as pd
import numpy as np

from gridlang.formulas import (
    SUMIF, COUNTIF, AVERAGEIF, SUMIFS, COUNTIFS,
    VLOOKUP, HLOOKUP, INDEX, MATCH, XLOOKUP,
    LEFT, RIGHT, MID, CONCATENATE, TRIM, UPPER, LOWER, PROPER, SUBSTITUTE, LEN,
    YEAR, MONTH, DAY, DATEDIF, NETWORKDAYS, TODAY, NOW,
    IF, IFS, SWITCH, AND, OR, NOT, IFERROR,
    ROUND, ROUNDUP, ROUNDDOWN, ABS, MOD, POWER, CEILING, FLOOR,
    RANK, PERCENTILE, QUARTILE, MEDIAN, STDEV, LARGE, SMALL,
    PIVOT, UNIQUE, SORT, FILTER, GROUPBY, TRANSPOSE,
    get_all_formulas,
)
from datetime import date, datetime


@pytest.fixture
def sales_df():
    return pd.DataFrame({
        'Product': ['Widget', 'Gadget', 'Tool', 'Widget', 'Gadget'],
        'Region': ['North', 'South', 'East', 'West', 'North'],
        'Revenue': [100, 200, 150, 120, 180],
        'Cost': [60, 120, 90, 70, 110],
    })


class TestStatisticalFunctions:
    def test_sumif_greater_than(self, sales_df):
        result = SUMIF(sales_df['Revenue'], ">150")
        assert result == 380  # 200 + 180

    def test_sumif_equals(self, sales_df):
        result = SUMIF(sales_df['Product'], "Widget", sales_df['Revenue'])
        assert result == 220  # 100 + 120

    def test_countif(self, sales_df):
        assert COUNTIF(sales_df['Product'], "Widget") == 2
        assert COUNTIF(sales_df['Revenue'], ">100") == 4

    def test_averageif(self, sales_df):
        result = AVERAGEIF(sales_df['Product'], "Gadget", sales_df['Revenue'])
        assert result == 190  # (200 + 180) / 2

    def test_sumifs(self, sales_df):
        result = SUMIFS(sales_df['Revenue'],
                       sales_df['Product'], "Widget",
                       sales_df['Revenue'], ">100")
        assert result == 120

    def test_countifs(self, sales_df):
        result = COUNTIFS(sales_df['Product'], "Gadget",
                         sales_df['Region'], "North")
        assert result == 1


class TestLookupFunctions:
    def test_vlookup_exact(self, sales_df):
        result = VLOOKUP("Gadget", sales_df, 3)
        assert result == 200  # First match, Revenue column

    def test_vlookup_not_found(self, sales_df):
        result = VLOOKUP("Missing", sales_df, 3)
        assert result is None

    def test_hlookup(self):
        df = pd.DataFrame({'A': [1, 2], 'B': [3, 4], 'C': [5, 6]})
        result = HLOOKUP('B', df, 1)
        assert result == 3

    def test_index(self, sales_df):
        assert INDEX(sales_df, 1, 1) == "Widget"
        assert INDEX(sales_df, 2, 3) == 200

    def test_match(self, sales_df):
        assert MATCH("Tool", sales_df['Product']) == 3
        assert MATCH("Missing", sales_df['Product']) == 0

    def test_xlookup(self, sales_df):
        assert XLOOKUP("Tool", sales_df['Product'], sales_df['Revenue']) == 150
        assert XLOOKUP("X", sales_df['Product'], sales_df['Revenue'], -1) == -1


class TestTextFunctions:
    def test_left(self):
        assert LEFT("Hello", 3) == "Hel"
        assert LEFT(None, 3) == ""

    def test_right(self):
        assert RIGHT("Hello", 2) == "lo"

    def test_mid(self):
        assert MID("Hello World", 7, 5) == "World"

    def test_concatenate(self):
        assert CONCATENATE("A", "B", "C") == "ABC"
        assert CONCATENATE("Hello", " ", "World") == "Hello World"

    def test_trim(self):
        assert TRIM("  hello  ") == "hello"

    def test_upper_lower_proper(self):
        assert UPPER("hello") == "HELLO"
        assert LOWER("HELLO") == "hello"
        assert PROPER("hello world") == "Hello World"

    def test_substitute(self):
        assert SUBSTITUTE("hello world", "world", "python") == "hello python"

    def test_len(self):
        assert LEN("hello") == 5
        assert LEN(None) == 0


class TestDateFunctions:
    def test_year_month_day(self):
        d = date(2024, 3, 15)
        assert YEAR(d) == 2024
        assert MONTH(d) == 3
        assert DAY(d) == 15

    def test_datedif(self):
        start = date(2024, 1, 1)
        end = date(2024, 6, 15)
        assert DATEDIF(start, end, "D") == 166
        assert DATEDIF(start, end, "M") == 5
        assert DATEDIF(start, end, "Y") == 0

    def test_networkdays(self):
        start = date(2024, 1, 1)
        end = date(2024, 1, 7)  # Mon-Sun
        result = NETWORKDAYS(start, end)
        assert result == 5  # Mon-Fri

    def test_today_now(self):
        assert isinstance(TODAY(), date)
        assert isinstance(NOW(), datetime)


class TestLogicFunctions:
    def test_if_scalar(self):
        assert IF(True, "yes", "no") == "yes"
        assert IF(False, "yes", "no") == "no"

    def test_if_series(self):
        series = pd.Series([True, False, True])
        result = IF(series, "Y", "N")
        assert list(result) == ["Y", "N", "Y"]

    def test_ifs(self):
        assert IFS(False, "A", True, "B", True, "C") == "B"

    def test_switch(self):
        assert SWITCH("B", "A", 1, "B", 2, "C", 3, 0) == 2
        assert SWITCH("X", "A", 1, "B", 2, 99) == 99

    def test_and_or_not(self):
        assert AND(True, True, True) is True
        assert AND(True, False) is False
        assert OR(False, True) is True
        assert NOT(True) is False

    def test_iferror(self):
        assert IFERROR(float('nan'), 0) == 0
        assert IFERROR(42, 0) == 42


class TestMathFunctions:
    def test_round(self):
        assert ROUND(3.456, 2) == 3.46
        assert ROUND(3.456, 0) == 3.0

    def test_roundup_rounddown(self):
        assert ROUNDUP(3.2, 0) == 4.0
        assert ROUNDDOWN(3.9, 0) == 3.0

    def test_abs(self):
        assert ABS(-5) == 5

    def test_mod(self):
        assert MOD(10, 3) == 1

    def test_power(self):
        assert POWER(2, 3) == 8

    def test_ceiling_floor(self):
        assert CEILING(4.3, 1) == 5.0
        assert FLOOR(4.9, 1) == 4.0


class TestStatFunctions:
    def test_rank(self):
        s = pd.Series([10, 30, 20])
        result = RANK(s, s, order=0)
        assert list(result) == [3, 1, 2]  # descending

    def test_percentile(self):
        s = pd.Series(range(100))
        assert PERCENTILE(s, 0.5) == pytest.approx(49.5)

    def test_quartile(self):
        s = pd.Series(range(100))
        assert QUARTILE(s, 2) == pytest.approx(49.5)

    def test_median(self):
        s = pd.Series([1, 2, 3, 4, 5])
        assert MEDIAN(s) == 3.0

    def test_large_small(self):
        s = pd.Series([10, 30, 20, 50, 40])
        assert LARGE(s, 1) == 50
        assert LARGE(s, 2) == 40
        assert SMALL(s, 1) == 10


class TestDataAnalysis:
    def test_pivot(self, sales_df):
        result = PIVOT(sales_df, index='Product', values='Revenue', aggfunc='sum')
        assert 'Product' in result.columns
        assert 'Revenue' in result.columns
        widget_row = result[result['Product'] == 'Widget']
        assert widget_row['Revenue'].iloc[0] == 220

    def test_unique(self):
        s = pd.Series([1, 2, 2, 3, 3, 3])
        result = UNIQUE(s)
        assert set(result) == {1, 2, 3}

    def test_sort(self, sales_df):
        result = SORT(sales_df, by='Revenue', ascending=False)
        assert result.iloc[0]['Revenue'] == 200

    def test_filter(self, sales_df):
        result = FILTER(sales_df, sales_df['Revenue'] > 150)
        assert len(result) == 2

    def test_groupby(self, sales_df):
        result = GROUPBY(sales_df, 'Product', {'Revenue': 'sum'})
        assert len(result) == 3  # Widget, Gadget, Tool

    def test_transpose(self):
        df = pd.DataFrame({'Label': ['A', 'B'], 'Val1': [1, 2], 'Val2': [3, 4]})
        result = TRANSPOSE(df)
        assert 'A' in result.columns

    def test_get_all_formulas(self):
        formulas = get_all_formulas()
        assert len(formulas) >= 50
        assert 'VLOOKUP' in formulas
        assert 'SUMIF' in formulas
        assert 'PIVOT' in formulas
