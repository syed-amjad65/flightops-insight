# FlightOps Insight - common tasks (Windows: use run.ps1 instead if make is absent)
PYTHON ?= python

.PHONY: help data train-otp train-rul optimize all app test clean

help:
	@echo "data        Build BTS/C-MAPSS parquet files and DuckDB"
	@echo "train-otp   Train and calibrate the OTP model"
	@echo "train-rul   Train the RUL models"
	@echo "optimize    Run CP-SAT scenarios"
	@echo "all         Run the full pipeline"
	@echo "app         Launch Streamlit"
	@echo "test        Run the unit test suite"

data:
	$(PYTHON) scripts/build_data.py

train-otp:
	$(PYTHON) scripts/train_otp.py

train-rul:
	$(PYTHON) scripts/train_rul.py

optimize:
	$(PYTHON) scripts/run_optimization.py

all:
	$(PYTHON) scripts/run_all.py

app:
	$(PYTHON) -m streamlit run app.py

test:
	$(PYTHON) -m pytest

clean:
	@echo "Manual cleanup only: processed data, artifacts and reports are not deleted automatically."
