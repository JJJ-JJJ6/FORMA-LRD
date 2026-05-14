光谱信息如下：

波长范围：
{{ wl_left }} Å – {{ wl_right }} Å

---

对光谱连续谱的定性描述为：
{{ continuum_description | tojson }}

---

对该光谱的发射/吸收特征的定性描述为：

{{ feature_description | tojson }}

---

该光谱的发射/吸收特征的具体信息：

- 峰
{% if peaks %}
{% for p in peaks %}

Wavelength: {{ p.wavelength }}
Amplitude: {{ p.amplitude }}
Amplitude rank: {{ p.amplitude_rank }}
Width in Å: {{ p.FWHM_A }}
Width in km/s: {{ p.FWHM_km_s }}
Width class: {{ p.width_class }}
Covered troughs (Is there any trough covered by the peak?): {{ p.covered_troughs }}
Covered trough centers: {{ p.trough_centers }}
Neighbors:
{{ p.left_neighbor }}
{{ p.right_neighbor }}
Does it touch the edge: {{ p.quality_boundary_touch }}
------------------------------------------------------
{% endfor %}
{% else %}
无显著峰特征
{% endif %}
------------------------------------------------------
------------------------------------------------------
- 谷
{% if troughs %}
{% for t in troughs %}

Wavelength: {{ t.wavelength }}
Amplitude: {{ t.amplitude }}
Amplitude rank: {{ t.amplitude_rank }}
Width in Å: {{ t.FWHM_A }}
Width in km/s: {{ t.FWHM_km_s }}
Neighbors:
{{ t.left_neighbor }}
{{ t.right_neighbor }}
-------------------------------------------------------
{% endfor %}
{% else %}
无显著谷特征
{% endif %}

---

## 各路径定量分析摘要

以下三组摘要由 其他 agent 分析产生，每组为一个 JSON 数组（至多 2 个假设元素）。若某路径未运行或所有假设均无法确证，对应字段为 null 或空数组。

---

### QSO 路径摘要（extract_QSO）

```json
{{ extract_QSO | tojson(indent=2) }}
```

{% if discussion_QSO %}
#### QSO 讨论记录（逐假设 critique ↔ patch 回应）

{% for d in discussion_QSO %}
**假设 {{ loop.index }}**

*Critique：*
{{ d.critique }}

*Patch 回应：*
{{ d.response }}

---
{% endfor %}
{% endif %}

---

### ELG 路径摘要（extract_ELG）

```json
{{ extract_ELG | tojson(indent=2) }}
```

{% if discussion_ELG %}
#### ELG 讨论记录（逐假设 critique ↔ patch 回应）

{% for d in discussion_ELG %}
**假设 {{ loop.index }}**

*Critique：*
{{ d.critique }}

*Patch 回应：*
{{ d.response }}

---
{% endfor %}
{% endif %}

---

### LRG 路径摘要（extract_LRG）

```json
{{ extract_LRG | tojson(indent=2) }}
```

{% if discussion_LRG %}
#### LRG 讨论记录（逐假设 critique ↔ patch 回应）

{% for d in discussion_LRG %}
**假设 {{ loop.index }}**

*Critique：*
{{ d.critique }}

*Patch 回应：*
{{ d.response }}

---
{% endfor %}
{% endif %}

---

请按 Step V-1 到 Step V-3 的顺序，完成跨类型综合裁决，给出最终 1-2 个最可能的结论。
