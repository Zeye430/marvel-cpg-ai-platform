"""Chart colours for every module (constants only, importable without the UI stack).

Red and white are the frame of the workspace (chrome, primary action, focus), not the colour of
the data. Charts use a multi-hue categorical palette in a fixed order, validated on the white card
surface (#ffffff): every adjacent pair is separable for colour-blind readers (worst CVD dE 9.1,
OKLab x100) and with normal vision (worst dE 19.6). Assign slots in order, never cycled past the
end; with all pairs on screen at once (scatter) use at most the first three. Slots 3-5 are below
3:1 contrast with the surface, so charts using them keep visible labels or a table view.
Brand red is the last slot, so it never reads as the default data colour.
"""

CHART_COLORS = ("#2a78d6",   # 1 blue (single-series default)
                "#eb6834",   # 2 orange
                "#1baf7a",   # 3 aqua
                "#eda100",   # 4 yellow
                "#e87ba4",   # 5 magenta
                "#008300",   # 6 green
                "#6250d6",   # 7 violet
                "#e34948")   # 8 red
CHART_NEUTRAL = "#a8a29e"    # unknown / baseline / reference marks: neutral, not a category hue
CHART_INK = "#211b1c"
