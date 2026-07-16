"""
Rest-frame spectral line tables — single source of truth.

Previously triplicated across utils/VI.py, harness/tools.py, and
harness/kb/lines.md (CLAUDE.md finding #4); consolidated here per that
finding's own suggestion ("acceptable structural change, keep it small").
utils/VI.py and harness/tools.py both import from this module now.
harness/kb/lines.md is a separate Markdown copy for the KB-grep/LLM-facing
side and must still be kept in sync by hand when this file changes.

⚠ Rest-NIR entries (LRD domain, Kapoor+26) are working values — verify
against NIST/SDSS vacuum wavelengths before the KB freeze (CLAUDE.md
Science constants).
"""

# 静止系发射线表（optical set kept in full — required as rival hypothesis
# lines for the LRD domain, per CLAUDE.md build order A2）
EMISSION_LINES = {
    # 高电离 / AGN 特征线
    "Lyα":       1216.0,
    "C IV":      1549.0,
    "He II":     1640.4,
    "C III]":    1909.0,
    "Mg II":     2800.0,
    "[Ne V]":    3426.0,
    "[O II]":    3727.0,
    # Balmer 系列
    "Hε":        3970.1,
    "Hδ":        4102.9,
    "Hγ":        4341.7,
    "Hβ":        4862.7,
    # 窄线区
    "[O III]a":  4960.3,
    "[O III]b":  5008.2,
    "[N II]a":   6549.8,
    "Hα":        6564.6,
    "[N II]b":   6585.3,
    "[S II]a":   6718.3,
    "[S II]b":   6732.7,

    # ── Rest-NIR (LRD domain, Kapoor+26 EIGER F356W; CLAUDE.md Science
    #    constants) — Paschen series + He I + [S III] + [Fe II] + O I 8446.
    #    High-order Paschen (Pa9-12) values are approximate per CLAUDE.md.
    "Pa12":      8753.0,
    "Pa11":      8865.0,
    "O I 8446":  8448.7,   # air label "O I 8446"; vacuum ~8448.7
    "Pa10":      9017.0,
    "Pa9":       9232.0,
    "[S III]a":  9071.0,
    "[Fe II]":   9179.0,   # paper's label
    "[S III]b":  9533.0,
    "Paδ":       10052.0,
    "He I":      10833.0,
    "Paγ":       10941.0,
    "Paβ":       12822.0,
    "Paα":       18756.0,
}

# 发射线宽窄分类：broad = 宽线区 (BLR) 允许的宽线，narrow = 窄线区 (NLR) 典型窄线
# both = BLR+NLR 均可产生，宽窄皆合理（Balmer 系在 QSO 中有 broad+narrow 叠加，
#        在 galaxy 中仅 narrow），宽度校验对 both 类跳过
# 用于匹配时检查寻峰宽度与物理期望是否一致
EMISSION_LINE_WIDTHS = {
    # BLR 宽线
    "Lyα":       "broad",
    "C IV":      "broad",
    "C III]":    "broad",
    "He II":     "both", # 在 QSO 中可以表现为 BLR 宽线，在低电离 AGN 或 Galaxy 中也可以是较窄的线
    "Mg II":     "broad",
    # Balmer 系列：QSO 中 broad+narrow 叠加，galaxy 中仅 narrow
    "Hε":        "both",
    "Hδ":        "both",
    "Hγ":        "both",
    "Hβ":        "both",
    "Hα":        "both",
    # NLR 窄线
    "[Ne V]":    "narrow",
    "[O II]":    "narrow",
    "[O III]a":  "narrow",
    "[O III]b":  "narrow",
    "[N II]a":   "narrow",
    "[N II]b":   "narrow",
    "[S II]a":   "narrow",
    "[S II]b":   "narrow",

    # ── Rest-NIR width classes ──
    # Paschen series treated like Balmer: broad+narrow superposition is the
    # central Kapoor+26 §4.2 diagnostic, so width validation is skipped
    # ("both") rather than pre-judging the LRD-vs-classical-AGN question.
    "Paα":       "both",
    "Paβ":       "both",
    "Paγ":       "both",
    "Paδ":       "both",
    "Pa9":       "both",
    "Pa10":      "both",
    "Pa11":      "both",
    "Pa12":      "both",
    # He I 10833 shows narrow, broad, and blueshifted-absorption components
    # across the sample (§4.2) — "both", not pre-classified.
    "He I":      "both",
    # O I 8446 is a Bowen-fluorescence line that can be BLR-broad in AGN
    # (not purely a PDR/NLR narrow feature) — treated as "both" pending
    # per-source verification.
    "O I 8446":  "both",
    # [S III] / [Fe II] are collisionally-excited forbidden lines, NLR-only,
    # same class as [O III]/[N II]/[S II] above.
    "[S III]a":  "narrow",
    "[S III]b":  "narrow",
    "[Fe II]":   "narrow",
}

ABSORPTION_LINES = {
    "Ca K_abs":      3934.8,
    "Ca H_abs":      3969.6,
    "G-band_abs":    4305.6,
    "Mg I_abs":        5176.7,
    "Mg II_abs":     2800.0,
    "Na D_abs":      5895.6,
    "CaT1_abs":      8498.0,
    "CaT2_abs":      8542.0,
    "CaT3_abs":      8662.0,
    # Balmer 吸收
    "Hε_abs":    3970.1,
    "Hδ_abs":    4102.9,
    "Hγ_abs":    4341.7,
    "Hβ_abs":    4862.7,
    "Hα_abs":    6564.6,
}

# Mg II_abs ≈ BLR 宽吸收线；其余为恒星/ISM 窄吸收
ABSORPTION_LINE_WIDTHS = {
    "Mg II_abs": "broad",
    # 其余默认 "absorption"（窄），不在表中列出
}
