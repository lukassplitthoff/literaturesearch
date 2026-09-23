# How are strongly driven cQED devices simulated with Floquet methods?

> How are strongly driven circuit-QED devices and couplers simulated with Floquet and
> Floquet-Markov methods, and what drive-induced effects do those simulations predict?

A methods question, posed with three reference papers (arXiv 2609.04704, 2511.05031,
2501.18025). They are the gold set in [`gold_set.json`](gold_set.json), not seeds.

```bash
python examples/floquet_cqed/search.py
```

Why the search is shaped the way it is -- gold set instead of seeds, criteria that screen
on the physics rather than the method, and a wave ranking that puts theory first -- is
commented in [`search.py`](search.py), next to each setting.
