以下是本次光谱分析的各阶段输出，请据此撰写最终报告。

---

## 第一部分：光谱基本信息

波长范围：{{ wl_left }} Å – {{ wl_right }} Å

连续谱定性描述：
{{ continuum_description | tojson }}

谱线特征定性描述：
{{ feature_description | tojson }}

该光谱的发射/吸收特征的具体信息：

- 峰
{% if peaks %}
{% for p in peaks %}

Wavelength: {{ p.wavelength }}
Wavelength_error: {{ p.wavelength_err }}
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
Wavelength_error: {{ t.wavelength_err }}
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

## 第二部分：前置分类结论

{{ preliminary_classification_monkey | tojson }}

---

## 第三部分：各路径定量分析摘要

### QSO 路径（extract_QSO）
```json
{{ extract_QSO | tojson(indent=2) }}
```

### ELG 路径（extract_ELG）
```json
{{ extract_ELG | tojson(indent=2) }}
```

### LRG/GBS 路径（extract_LRG）
```json
{{ extract_LRG | tojson(indent=2) }}
```

---

## 第四部分：跨类型综合裁决

{{ verdict }}

---

请按报告格式（第 1–6 节）撰写完整最终报告。
