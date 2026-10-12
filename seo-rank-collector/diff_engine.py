"""SERP Rank Diff Engine for SEO Rank Collector.

Compares historical SERPSnapshots to calculate rank movement (gained, lost, improved, declined, unchanged).
"""
from typing import List
from rank_models import SERPSnapshot, RankDelta


class SERPDiffEngine:
    """Calculates ranking changes between two SERP snapshot states."""

    @staticmethod
    def compare_snapshots(old_snapshot: SERPSnapshot, new_snapshot: SERPSnapshot) -> List[RankDelta]:
        """Compares old_snapshot and new_snapshot, returning position deltas."""
        old_map = {
            item.canonical_url: (item.url, item.domain, item.organic_position)
            for item in old_snapshot.items if item.organic_position is not None
        }
        new_map = {
            item.canonical_url: (item.url, item.domain, item.organic_position)
            for item in new_snapshot.items if item.organic_position is not None
        }

        all_canonicals = set(old_map.keys()) | set(new_map.keys())
        deltas: List[RankDelta] = []

        for canonical in all_canonicals:
            old_info = old_map.get(canonical)
            new_info = new_map.get(canonical)

            raw_url = new_info[0] if new_info else (old_info[0] if old_info else canonical)
            domain = new_info[1] if new_info else (old_info[1] if old_info else "")

            prev_pos = old_info[2] if old_info else None
            curr_pos = new_info[2] if new_info else None

            if prev_pos is None and curr_pos is not None:
                change = "gained"
                delta_val = None
            elif prev_pos is not None and curr_pos is None:
                change = "lost"
                delta_val = None
            elif prev_pos == curr_pos:
                change = "unchanged"
                delta_val = 0
            elif curr_pos < prev_pos:
                change = "improved"
                delta_val = prev_pos - curr_pos  # positive number means moved up (e.g. 5 -> 2 = +3)
            else:
                change = "declined"
                delta_val = prev_pos - curr_pos  # negative number means dropped (e.g. 2 -> 5 = -3)

            deltas.append(
                RankDelta(
                    url=raw_url,
                    canonical_url=canonical,
                    domain=domain,
                    previous_position=prev_pos,
                    current_position=curr_pos,
                    change=change,
                    delta=delta_val,
                )
            )

        # Sort deltas by current position, then previous position
        deltas.sort(key=lambda d: (d.current_position if d.current_position else 999, d.previous_position if d.previous_position else 999))
        return deltas
