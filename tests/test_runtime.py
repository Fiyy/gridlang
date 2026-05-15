"""Tests for gridlang.runtime"""

import pytest
import pandas as pd
import numpy as np

from gridlang.runtime import execute, RuntimeError_, ExecutionResult


@pytest.fixture
def sample_df():
    """Sample DataFrame for testing."""
    return pd.DataFrame({
        'Name': ['Alice', 'Bob', 'Charlie'],
        'Score': [90, 85, 78],
        'Subject': ['Math', 'Science', 'Math'],
    })


class TestExecuteTransform:
    """Test the transform function execution."""

    def test_basic_transform(self, sample_df):
        code = """
def transform(df):
    df['Grade'] = df['Score'].apply(lambda x: 'A' if x >= 90 else 'B' if x >= 80 else 'C')
    return df
"""
        result = execute(code, sample_df)
        assert isinstance(result, ExecutionResult)
        assert 'Grade' in result.df.columns
        assert result.df.iloc[0]['Grade'] == 'A'
        assert result.df.iloc[1]['Grade'] == 'B'
        assert result.df.iloc[2]['Grade'] == 'C'

    def test_empty_compute(self, sample_df):
        """Empty compute passes through data unchanged."""
        result = execute("", sample_df)
        pd.testing.assert_frame_equal(result.df, sample_df)
        assert result.compute_functions == []

    def test_transform_with_pandas(self, sample_df):
        code = """
def transform(df):
    df['Normalized'] = (df['Score'] - df['Score'].min()) / (df['Score'].max() - df['Score'].min())
    return df
"""
        result = execute(code, sample_df)
        assert 'Normalized' in result.df.columns
        assert result.df['Normalized'].max() == 1.0
        assert result.df['Normalized'].min() == 0.0

    def test_transform_with_numpy(self, sample_df):
        code = """
def transform(df):
    df['Log_Score'] = np.log(df['Score'])
    return df
"""
        result = execute(code, sample_df)
        assert 'Log_Score' in result.df.columns
        assert abs(result.df.iloc[0]['Log_Score'] - np.log(90)) < 0.001

    def test_transform_returns_none(self, sample_df):
        code = """
def transform(df):
    df['New'] = 1
    # Forgot return!
"""
        with pytest.raises(RuntimeError_, match="returned None"):
            execute(code, sample_df)

    def test_transform_returns_wrong_type(self, sample_df):
        code = """
def transform(df):
    return [1, 2, 3]
"""
        with pytest.raises(RuntimeError_, match="must return a DataFrame"):
            execute(code, sample_df)


class TestExecuteAggregates:
    """Test the aggregates function execution."""

    def test_basic_aggregates(self, sample_df):
        code = """
def transform(df):
    return df

def aggregates(df):
    return {
        'mean_score': df['Score'].mean(),
        'count': len(df),
    }
"""
        result = execute(code, sample_df)
        assert result.aggregates['mean_score'] == pytest.approx(84.333, rel=0.01)
        assert result.aggregates['count'] == 3

    def test_aggregates_receives_transformed_df(self, sample_df):
        code = """
def transform(df):
    df['Doubled'] = df['Score'] * 2
    return df

def aggregates(df):
    return {'has_doubled': 'Doubled' in df.columns}
"""
        result = execute(code, sample_df)
        assert result.aggregates['has_doubled'] is True

    def test_aggregates_optional(self, sample_df):
        code = """
def transform(df):
    return df
"""
        result = execute(code, sample_df)
        assert result.aggregates == {}

    def test_aggregates_returns_wrong_type(self, sample_df):
        code = """
def transform(df):
    return df

def aggregates(df):
    return "not a dict"
"""
        with pytest.raises(RuntimeError_, match="must return a dict"):
            execute(code, sample_df)


class TestExecuteValidate:
    """Test the validate function execution."""

    def test_validate_passes(self, sample_df):
        code = """
def validate(df):
    return []

def transform(df):
    return df
"""
        result = execute(code, sample_df)
        assert result.validation_messages == []

    def test_validate_fails(self, sample_df):
        code = """
def validate(df):
    errors = []
    if df['Score'].min() < 0:
        errors.append("Scores cannot be negative")
    return errors

def transform(df):
    return df
"""
        # Should pass with our sample data
        result = execute(code, sample_df)
        assert result.validation_messages == []

    def test_validate_blocks_execution(self):
        df = pd.DataFrame({'Score': [-5, 80, 90]})
        code = """
def validate(df):
    errors = []
    if (df['Score'] < 0).any():
        errors.append("Scores cannot be negative")
    return errors

def transform(df):
    return df
"""
        with pytest.raises(RuntimeError_, match="Validation failed"):
            execute(code, df)


class TestSandboxSecurity:
    """Test that dangerous operations are blocked."""

    def test_blocked_file_open(self, sample_df):
        code = """
def transform(df):
    f = open('/etc/passwd', 'r')
    return df
"""
        with pytest.raises(RuntimeError_):
            execute(code, sample_df)

    def test_blocked_import(self, sample_df):
        code = """
import os

def transform(df):
    os.system('echo hacked')
    return df
"""
        with pytest.raises(RuntimeError_):
            execute(code, sample_df)

    def test_blocked_subprocess(self, sample_df):
        code = """
def transform(df):
    import subprocess
    subprocess.run(['ls'])
    return df
"""
        with pytest.raises(RuntimeError_):
            execute(code, sample_df)

    def test_allowed_safe_modules(self, sample_df):
        code = """
import math
import statistics
from datetime import datetime

def transform(df):
    df['Sqrt_Score'] = df['Score'].apply(math.sqrt)
    return df
"""
        result = execute(code, sample_df)
        assert 'Sqrt_Score' in result.df.columns

    def test_dataframe_isolation(self, sample_df):
        """Ensure original df is not modified."""
        original_cols = list(sample_df.columns)
        code = """
def transform(df):
    df['New_Col'] = 999
    return df
"""
        execute(code, sample_df)
        assert list(sample_df.columns) == original_cols


class TestExecutionMetadata:
    """Test execution result metadata."""

    def test_compute_functions_list(self, sample_df):
        code = """
def validate(df):
    return []

def transform(df):
    return df

def aggregates(df):
    return {}
"""
        result = execute(code, sample_df)
        assert 'validate' in result.compute_functions
        assert 'transform' in result.compute_functions
        assert 'aggregates' in result.compute_functions

    def test_syntax_error_in_compute(self, sample_df):
        code = """
def transform(df)
    return df
"""
        with pytest.raises(RuntimeError_, match="Syntax error"):
            execute(code, sample_df)

    def test_runtime_error_in_transform(self, sample_df):
        code = """
def transform(df):
    return df['nonexistent_column']
"""
        with pytest.raises(RuntimeError_, match="Error in transform"):
            execute(code, sample_df)
