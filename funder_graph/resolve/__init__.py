"""Entity resolution: turning recipient strings into EINs, with published confidence.

``normalize`` is the string canon every matcher and every published
``recipient_name_normalized`` share. The matcher itself, the blocking, and the confidence
tiers land in milestone 4. Any change to anything here must move precision and recall on
``tests/fixtures/matching/labeled_pairs.csv`` in the right direction and show the numbers.
"""
