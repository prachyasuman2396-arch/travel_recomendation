# EDA

The reproducible EDA lives in code so it can be re-run after every ingestion:

```bash
cd backend
python scripts/run_eda.py        # writes data/reports/eda_<timestamp>/
```

`eda.ipynb` in this folder loads the same frames for interactive exploration.
All numbers come from the database. Rows with `data_origin = mock` are synthetic.
