"""Source for the MatchResult2 class.

This should be the default response from any `match` method.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    DefaultDict,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    cast,
)

from sqlfluff.core.slice_helpers import slice_length

if TYPE_CHECKING:  # pragma: no cover
    from sqlfluff.core.parser.segments import BaseSegment, MetaSegment


@dataclass(frozen=True)
class MatchResult2:
    """This should be the NEW default response from any `match` method.

    All references and indices are in reference to a single root tuple
    of segments. This result contains enough information to actually
    create the nested tree structure, but shouldn't actually contain
    any new segments itself. That means keeping information about:
    1. Ranges of segments which should be included segments to be
       created.
    2. References to the segment classes which we would create.
    3. Information about any _new_ segments to add in the process,
       such as MetaSegment classes.

    Given the segments aren't yet "nested", the structure of this
    result *will* need to be nested, ideally self nested.

    In the case of finding unparsable locations, we should return the
    "best" result, referencing the furthest that we got. That allows
    us to identify those parsing issues and create UnparsableSegment
    classes later.
    """

    # Slice in the reference tuple
    matched_slice: slice
    # Reference to the kind of segment to create.
    # NOTE: If this is null, it means we've matched a sequence of segments
    # but not yet created a container to put them in.
    matched_class: Optional[Type["BaseSegment"]] = None
    # kwargs to pass to the segment on creation.
    segment_kwargs: Dict[str, Any] = field(default_factory=dict)
    # Types and indices to add in new segments (they'll be meta segments)
    insert_segments: Tuple[Tuple[int, Type["MetaSegment"]], ...] = field(
        default_factory=tuple
    )
    # Child segment matches (this is the recursive bit)
    child_matches: Tuple["MatchResult2", ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return slice_length(self.matched_slice)

    def __bool__(self):
        """Evaluate this MatchResult2 for whether it counts as a clean match.

        A MatchResult2 is truthy if it has:
        - matched segments
        - or has inserts.
        """
        return len(self) > 0 or bool(self.insert_segments)

    def stringify(self, indent=""):
        """Pretty print a match for debugging.

        TODO: Needs tests (and probably being used more).
        """
        prefix = f"Match ({self.matched_class}): {self.matched_slice}"
        buffer = prefix
        for key, value in self.segment_kwargs.items():
            buffer += f"\n  {indent}-{key}: {value!r}"
        if self.insert_segments:
            for idx, insert in self.insert_segments:
                buffer += f"\n  {indent}+{idx}: {insert}"
        if self.child_matches:
            for child in self.child_matches:
                buffer += f"\n  {indent}+{child.stringify(indent + '  ')}"
        return buffer

    @classmethod
    def empty_at(cls, idx):
        """Create an empty match at a particular index.

        An empty match is by definition, unclean.
        """
        return cls(slice(idx, idx))

    def is_better_than(self, other: "MatchResult2") -> bool:
        """A match is better compared on length and cleanliness."""
        return len(self) > len(other)

    def append(
        self,
        other: "MatchResult2",
        insert_segments: Tuple[Tuple[int, Type["MetaSegment"]], ...] = (),
    ) -> "MatchResult2":
        """Combine another subsequent match onto this one.

        NOTE: Because MatchResult2 is frozen, this returns a new
        match.
        """
        # If the current match is empty, just return the other.
        if not len(self) and not self.insert_segments:
            return other
        # If the same is true of the other, just return self.
        if not len(other) and not other.insert_segments:
            return self

        # Otherwise the two must follow each other.
        # NOTE: A gap is allowed, but is assumed to be included in the
        # match.
        assert self.matched_slice.stop <= other.matched_slice.start
        new_slice = slice(self.matched_slice.start, other.matched_slice.stop)
        child_matches = ()
        for match in (self, other):
            # If it's got a matched class, add it as a child.
            if match.matched_class:
                child_matches += (match,)
            # Otherwise incorporate
            else:
                # Note: We're appending to the optional insert segments
                # provided in the kwargs.
                insert_segments += match.insert_segments
                child_matches += match.child_matches
        return MatchResult2(
            new_slice,
            insert_segments=insert_segments,
            child_matches=child_matches,
        )

    def wrap(
        self,
        outer_class: Type["BaseSegment"],
        insert_segments: Tuple[Tuple[int, Type["MetaSegment"]], ...] = (),
        segment_kwargs: Dict[str, Any] = {},
    ) -> "MatchResult2":
        """Wrap this result with an outer class.

        NOTE: Because MatchResult2 is frozen, this returns a new
        match.
        """
        if self.matched_class:
            # If the match already has a class, then make
            # the current one and child match and clear the
            # other buffers.
            child_matches = (self,)
        else:
            # Otherwise flatten the existing match into
            # the new one.
            insert_segments = self.insert_segments + insert_segments
            child_matches = self.child_matches

        # Otherwise flatten the content
        return MatchResult2(
            self.matched_slice,
            matched_class=outer_class,
            segment_kwargs=segment_kwargs,
            insert_segments=insert_segments,
            child_matches=child_matches,
        )

    def apply(self, segments: Tuple["BaseSegment", ...]) -> Tuple["BaseSegment", ...]:
        """Actually this match to segments to instantiate.

        This turns a theoretical match into a nested structure of segments.

        We handle child segments _first_ so that we can then include them when
        creating the parent. That means sequentially working through the children
        and any inserts. If there are overlaps, then we have a problem, and we
        should abort.
        """
        if not slice_length(self.matched_slice):
            # TODO: Review whether we should handle any of these
            # scenarios ()
            assert not self.matched_class, (
                "Tried to apply zero length MatchResult2 with "
                "`matched_class`. This MatchResult2 is invalid."
            )
            assert not self.child_matches, (
                "Tried to apply zero length MatchResult2 with "
                "`child_matches`. Is this allowed?! "
                f"Result: {self}"
            )
            assert not self.insert_segments, (
                "Tried to apply zero length MatchResult2 with "
                "`insert_segments`. This situation isn't handled yet."
            )
            return ()

        assert len(segments) >= self.matched_slice.stop, (
            f"Matched slice ({self.matched_slice}) sits outside segment "
            f"bounds: {len(segments)}"
        )

        # Which are the locations we need to care about?
        trigger_locs: DefaultDict[
            int, List[MatchResult2, Type["MetaSegment"]]
        ] = defaultdict(list)
        # Add the inserts first...
        for insert in self.insert_segments:
            trigger_locs[insert[0]].append(insert[1])
        # ...and then the matches
        for match in self.child_matches:
            trigger_locs[match.matched_slice.start].append(match)

        # Then work through creating any subsegments.
        result_segments = ()
        max_idx = self.matched_slice.start
        for idx in sorted(trigger_locs.keys()):
            # Have we passed any untouched segments?
            if idx > max_idx:
                # If so, add them in unchanged.
                result_segments += segments[max_idx:idx]
                max_idx = idx
            elif idx < max_idx:  # pragma: no cover
                raise ValueError("SKIP AHEAD ERROR")
            # Then work through each of the triggers.
            for trigger in trigger_locs[idx]:
                # If it's a segment, instantiate it.
                if isinstance(trigger, MatchResult2):
                    result_segments += trigger.apply(segments=segments)
                    # Update the end slice.
                    max_idx = trigger.matched_slice.stop
                    continue

                # Otherwise it's a segment.
                seg_type = cast("MetaSegment", trigger)
                # Get the location from the next segment unless there isn't one.
                if idx < len(segments):
                    _pos = segments[idx].pos_marker.start_point_marker()
                else:
                    _pos = segments[idx - 1].pos_marker.end_point_marker()
                result_segments += (seg_type(pos_marker=_pos),)

        # If we finish working through the triggers and there's
        # still something left, then add that too.
        if max_idx < self.matched_slice.stop:
            result_segments += segments[max_idx : self.matched_slice.stop]

        if not self.matched_class:
            return result_segments

        # Otherwise construct the subsegment
        if self.matched_class.class_is_type("raw"):
            assert len(result_segments) == 1
            # TODO: Should this be a generic method on BaseSegment and RawSegment?
            # It feels a little strange to be this specific here.
            new_seg = self.matched_class(
                raw=result_segments[0].raw,
                pos_marker=result_segments[0].pos_marker,
                **self.segment_kwargs,
            )
        else:
            new_seg = self.matched_class(
                segments=result_segments, **self.segment_kwargs
            )
        return (new_seg,)

    def _to_old_match_result(self, segments):
        pass
