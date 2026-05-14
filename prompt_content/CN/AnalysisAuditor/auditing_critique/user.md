光谱信息如下：

波长范围：{{ wl_left }} Å – {{ wl_right }} Å

对光谱连续谱的定性描述为：
{{ continuum_description | tojson }}

对该光谱的发射/吸收特征的定性描述为：
{{ feature_description | tojson }}

---

## 待审查的分析路径：{{ source_path }}

**假设 {{ hypothesis_index }}/{{ hypothesis_total }}** — 该路径下由其他 agent 生成的定量分析假设：

```json
{{ hypothesis | tojson(indent=2) }}
```

---

{% if debate_history %}
## 历史讨论记录（本假设）

以下是前几轮讨论中针对本假设的 critique 与 patch 回应记录，请以此为上下文参考：
- **不要**重复已被前轮回应充分解答的质疑
- 若前轮质疑仍未解决，可继续追问
- 聚焦于前轮讨论中涌现的**新角度**或**未解决的问题**

{% for d in debate_history %}
### 第 {{ d.round }} 轮
**Critique：**
{{ d.critique }}

**Response：**
{{ d.response }}

---
{% endfor %}
{% endif %}

请对上述 {{ source_path }} 路径的假设进行批判性复核，指出 1 到 4 条具体质疑点，并给出总体评价。
