"""Common test fixtures for grammar testing."""

from typing import Any, Dict, List, Tuple, Type

import pytest

from sqlfluff.core.parser import KeywordSegment, StringParser
from sqlfluff.core.parser.context import ParseContext
from sqlfluff.core.parser.grammar.base import BaseGrammar
from sqlfluff.core.parser.types import ParseMode


@pytest.fixture(scope="function")
def structural_parse_mode_test(generate_test_segments, fresh_ansi_dialect):
    """Test the structural function of a grammar in various parse modes.

    This helper fixture is designed to modularise grammar tests.
    """

    def _structural_parse_mode_test(
        test_segment_seeds: List[str],
        grammar_class: Type[BaseGrammar],
        grammar_argument_seeds: List[str],
        grammar_terminator_seeds: List[str],
        grammar_kwargs: Dict[str, Any],
        parse_mode: ParseMode,
        input_slice: slice,
        output_tuple: Tuple[Any, ...],
    ):
        segments = generate_test_segments(test_segment_seeds)
        # Dialect is required here only to have access to bracket segments.
        ctx = ParseContext(dialect=fresh_ansi_dialect)

        _seq = grammar_class(
            *(StringParser(e, KeywordSegment) for e in grammar_argument_seeds),
            parse_mode=parse_mode,
            terminators=[
                StringParser(e, KeywordSegment) for e in grammar_terminator_seeds
            ],
            **grammar_kwargs,
        )
        _start = input_slice.start or 0
        _stop = input_slice.stop or len(segments)
        _match = _seq.match(segments[:_stop], _start, ctx)
        # If we're expecting an output tuple, assert the match is truthy.
        if output_tuple:
            assert _match
        _result = tuple(
            e.to_tuple(show_raw=True, code_only=False) for e in _match.apply(segments)
        )
        assert _result == output_tuple

    # Return the function
    return _structural_parse_mode_test
