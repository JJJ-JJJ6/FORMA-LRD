Below are the stage outputs of this spectral analysis. Please write the final report accordingly.

---

## Part 1: Basic Spectral Information

Wavelength range: {{ wl_left }} Å – {{ wl_right }} Å

Qualitative description of the continuum:
{{ continuum_description | tojson }}

Qualitative description of spectral line features:
{{ feature_description | tojson }}

Specific information on the spectrum's emission/absorption features:

- Peaks
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
No significant peak features
{% endif %}
------------------------------------------------------
------------------------------------------------------
- Troughs
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
No significant trough features
{% endif %}

---

## Part 2: Preliminary Classification Conclusion

{{ preliminary_classification_monkey | tojson }}

---

## Part 3: Summary of Quantitative Analysis by Path

### QSO Path (extract_QSO)
```json
{{ extract_QSO | tojson(indent=2) }}
```

### ELG Path (extract_ELG)
```json
{{ extract_ELG | tojson(indent=2) }}
```

### LRG/BGS Path (extract_LRG)
```json
{{ extract_LRG | tojson(indent=2) }}
```

---

## Part 4: Cross-Type Comprehensive Adjudication

{{ verdict }}

---

Please write the complete final report according to the report format (Sections 1–6).
