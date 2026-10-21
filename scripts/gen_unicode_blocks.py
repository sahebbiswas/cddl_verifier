#!/usr/bin/env python3
"""Regenerate ``src/cddl_verifier/_cddl/unicode_blocks.py``'s ``BLOCKS`` table (#231).

The block list comes from the Unicode Character Database through ``fontTools``
(``pip install fonttools``); names are the Unicode block names with the spaces removed,
as the W3C Note "Unicode Blocks and Categories in XML Schema" writes them. Run it and
paste the output over ``BLOCKS``; the aliases below the table are kept by hand.
"""

from fontTools.unicodedata import Blocks

bounds = list(Blocks.RANGES) + [0x110000]
print('BLOCKS: Dict[str, Tuple[int, int]] = {')
for i, name in enumerate(Blocks.VALUES):
    if name != 'No_Block':
        print(f"    '{name.replace(' ', '')}': (0x{bounds[i]:04X}, 0x{bounds[i + 1] - 1:04X}),")
print('}')
