光谱信息如下：

波长范围：{{ wl_left }} Å – {{ wl_right }} Å

对光谱连续谱的定性描述为：
{{ continuum_description | tojson }}

对该光谱的发射/吸收特征的定性描述为：
{{ feature_description | tojson }}

---

## 详细峰/谷信息

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
Covered troughs: {{ p.covered_troughs }}
Covered trough centers: {{ p.trough_centers }}
Neighbors: {{ p.left_neighbor }} | {{ p.right_neighbor }}
Touches edge: {{ p.quality_boundary_touch }}
--------------------------------------------------
{% endfor %}
{% else %}
无显著峰特征
{% endif %}

- 谷
{% if troughs %}
{% for t in troughs %}
Wavelength: {{ t.wavelength }}
Wavelength_error: {{ t.wavelength_err }}
Amplitude: {{ t.amplitude }}
Amplitude rank: {{ t.amplitude_rank }}
Width in Å: {{ t.FWHM_A }}
Width in km/s: {{ t.FWHM_km_s }}
Neighbors: {{ t.left_neighbor }} | {{ t.right_neighbor }}
--------------------------------------------------
{% endfor %}
{% else %}
无显著谷特征
{% endif %}

---

## 待审查的假设：{{ source_path }}（{{ hypothesis_index }}/{{ hypothesis_total }}）

该定量分析假设如下：

```json
{{ hypothesis | tojson(indent=2) }}
```

---

## 审查员质疑意见

{{ critique }}

---

请对上述质疑逐条提供文本回应。
