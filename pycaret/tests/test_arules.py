import os, sys
sys.path.insert(0, os.path.abspath(".."))

import pandas as pd
import pytest
import pycaret.arules
import pycaret.datasets

def test():
    # loading dataset
    data = pycaret.datasets.get_data("france")
    assert isinstance(data, pd.core.frame.DataFrame)

    # init setup
    arul101 = pycaret.arules.setup(data = data, transaction_id = "InvoiceNo", item_id = "Description", session_id = 123)
    assert isinstance(arul101, tuple)

    # create model
    model = pycaret.arules.create_model()
    assert isinstance(model, pd.core.frame.DataFrame)

    # get rules
    rules = pycaret.arules.get_rules(data = data, transaction_id = "InvoiceNo", item_id = "Description")
    assert isinstance(rules, pd.core.frame.DataFrame)

    assert 1 == 1

if __name__ == "__main__":
    test()
