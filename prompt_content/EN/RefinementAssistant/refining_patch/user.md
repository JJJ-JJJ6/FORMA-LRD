Spectral information is as follows:

Wavelength range: {{ wl_left }} Å – {{ wl_right }} Å

Qualitative description of the spectrum continuum:
{{ continuum_description | tojson }}

Qualitative description of the emission/absorption features of the spectrum:
{{ feature_description | tojson }}

---

## Detailed Peak/Trough Information

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
Covered troughs: {{ p.covered_troughs }}
Covered trough centers: {{ p.trough_centers }}
Neighbors: {{ p.left_neighbor }} | {{ p.right_neighbor }}
Touches edge: {{ p.quality_boundary_touch }}
--------------------------------------------------
{% endfor %}
{% else %}
No significant peak features
{% endif %}

- Troughs
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
No significant trough features
{% endif %}

---

## Hypothesis under Review: {{ source_path }} ({{ hypothesis_index }}/{{ hypothesis_total }})

The quantitative analysis hypothesis is as follows:

```json
{{ hypothesis | tojson(indent=2) }}
```

---

## Reviewer's Critique Comments

{{ critique }}

---

Please provide a text response to the above critiques, point by point.