import datetime
import gc
import logging
import os
import time
import traceback
import warnings
from typing import Any, Dict, List, Optional, Tuple, Union
from copy import deepcopy

import numpy as np  # type: ignore
import pandas as pd  # type: ignore
from IPython.utils import io
from sklearn.base import clone  # type: ignore
from sktime.forecasting.base import ForecastingHorizon
from sktime.forecasting.model_selection import (  # type: ignore
    ExpandingWindowSplitter,
    SlidingWindowSplitter,
)
from sktime.forecasting.base import BaseForecaster
from sktime.forecasting.base._sktime import DEFAULT_ALPHA  # type: ignore

from sktime.forecasting.naive import NaiveForecaster  # type: ignore

# from sktime.forecasting.compose import ForecastingPipeline
from pycaret.utils.time_series.forecasting.pipeline import (
    PyCaretForecastingPipeline,
    _add_model_to_pipeline,
)
from sktime.forecasting.compose import TransformedTargetForecaster

from pycaret.internal.preprocess.time_series.forecasting.preprocessor import (
    TSForecastingPreprocessor,
)

import pycaret.containers.metrics.time_series
import pycaret.containers.models.time_series
import pycaret.internal.patches.sklearn
import pycaret.internal.persistence
import pycaret.internal.preprocess
from pycaret.internal.Display import Display
from pycaret.internal.distributions import get_base_distributions
from pycaret.internal.logging import get_logger

# from pycaret.internal.pipeline import get_pipeline_fit_kwargs
from pycaret.internal.plots.time_series import _get_plot
from pycaret.internal.pycaret_experiment.supervised_experiment import (
    _SupervisedExperiment,
)
from pycaret.internal.pycaret_experiment.utils import MLUsecase, highlight_setup
from pycaret.internal.tests.time_series import run_test
from pycaret.internal.tunable import TunableMixin
from pycaret.internal.utils import color_df, deep_clone
from pycaret.internal.validation import is_sklearn_cv_generator
from pycaret.utils import _coerce_empty_dataframe_to_none, _resolve_dict_keys
from pycaret.utils.datetime import coerce_datetime_to_period_index
from pycaret.utils.time_series import TSModelTypes, get_sp_from_str
from pycaret.utils.time_series.forecasting import (
    get_predictions_with_intervals,
    update_additional_scorer_kwargs,
)
from pycaret.utils.time_series import TSApproachTypes, TSExogenousPresent
from pycaret.utils.time_series.forecasting.model_selection import (
    ForecastingGridSearchCV,
    ForecastingRandomizedSearchCV,
    cross_validate,
)
from pycaret.internal.plots.utils.time_series import _resolve_renderer

warnings.filterwarnings("ignore")
LOGGER = get_logger()


class TSForecastingExperiment(_SupervisedExperiment, TSForecastingPreprocessor):
    def __init__(self) -> None:
        super().__init__()
        self._ml_usecase = MLUsecase.TIME_SERIES
        self.exp_name_log = "ts-default-name"

        # Values in variable_keys are accessible in globals
        self.variable_keys = self.variable_keys.difference(
            {
                "target_param",
                "iterative_imputation_iters_param",
                "imputation_regressor",
                "imputation_classifier",
                "fold_shuffle_param",
                "stratify_param",
                "fold_groups_param",
            }
        )
        self.variable_keys = self.variable_keys.union(
            {
                "fh",
                "seasonal_period",
                "seasonality_present",
                "primary_sp_to_use",
                "all_sp_values",
                "strictly_positive",
                "enforce_pi",
                "enforce_exogenous",
                "approach_type",
                "exogenous_present",
                "index_type",
                "y_transformed",
                "X_transformed",
                "y_train_transformed",
                "X_train_transformed",
                "y_test_transformed",
                "X_test_transformed",
            }
        )
        self._available_plots = {
            "ts": "Time Series Plot",
            "train_test_split": "Train Test Split",
            "cv": "Cross Validation",
            "acf": "Auto Correlation (ACF)",
            "pacf": "Partial Auto Correlation (PACF)",
            "decomp": "Classical Decomposition",
            "decomp_stl": "STL Decomposition",
            "diagnostics": "Diagnostics Plot",
            "diff": "Difference Plot",
            "forecast": "Out-of-Sample Forecast Plot",
            "insample": "In-Sample Forecast Plot",
            "residuals": "Residuals Plot",
            "periodogram": "Frequency Components (Periodogram)",
            "fft": "Frequency Components (FFT)",
            "ccf": "Cross Correlation (CCF)",
        }

        available_plots_common_keys = [
            "ts",
            "train_test_split",
            "cv",
            "acf",
            "pacf",
            "diagnostics",
            "decomp",
            "decomp_stl",
            "diff",
            "periodogram",
            "fft",
            "ccf",
        ]
        self._available_plots_data_keys = available_plots_common_keys
        self._available_plots_estimator_keys = available_plots_common_keys + [
            "forecast",
            "insample",
            "residuals",
        ]

    def _get_setup_display(self, **kwargs) -> pd.DataFrame:
        """Returns the dataframe to be displayed at the end of setup"""

        display_container = [
            ["session_id", self.seed],
            ["Target", self.target_param],
            ["Approach", self.approach_type.value],
            ["Exogenous Variables", self.exogenous_present.value],
            ["Data shape", self.data.shape],
            ["Train data shape", self.train.shape],
            ["Test data shape", self.test.shape],
            ["Fold Generator", type(self.fold_generator).__name__],
            ["Fold Number", self.fold_param],
            ["Enforce Prediction Interval", self.enforce_pi],
            ["Seasonal Period(s) Tested", self.seasonal_period],
            ["Seasonality Present", self.seasonality_present],
            ["Seasonalities Detected", self.all_sp_values],
            ["Primary Seasonality", self.primary_sp_to_use],
            ["Target Strictly Positive", self.strictly_positive],
            ["Target White Noise", self.white_noise],
            ["Recommended d", self.lowercase_d],
            ["Recommended Seasonal D", self.uppercase_d],
            ["Missing Values", self.data.isna().sum().sum()],
            ["Preprocess", self.preprocess],
            ["CPU Jobs", self.n_jobs_param],
            ["Use GPU", self.gpu_param],
            ["Log Experiment", self.logging_param],
            ["Experiment Name", self.exp_name_log],
            ["USI", self.USI],
            # ["Transformed Train Target", self.y_train.shape],
            # ["Transformed Test Target", self.y_test.shape],
            # ["Transformed Train Exogenous", self.X_train.shape],
            # ["Transformed Test Exogenous", self.X_test.shape],
        ]

        # if self.preprocess:
        #     display_container.extend([["Imputation Type", self.imputation_type]])

        display_container = pd.DataFrame(
            display_container, columns=["Description", "Value"]
        )

        return display_container

    def _get_models(self, raise_errors: bool = True) -> Tuple[dict, dict]:
        all_models = {
            k: v
            for k, v in pycaret.containers.models.time_series.get_all_model_containers(
                self, raise_errors=raise_errors
            ).items()
            if not v.is_special
        }
        all_models_internal = (
            pycaret.containers.models.time_series.get_all_model_containers(
                self, raise_errors=raise_errors
            )
        )
        return all_models, all_models_internal

    def _get_metrics(self, raise_errors: bool = True) -> dict:
        """Gets the metrics for the Time Series Module

        Parameters
        ----------
        raise_errors : bool, optional
            [description], by default True

        Returns
        -------
        dict
            [description]
        """
        return pycaret.containers.metrics.time_series.get_all_metric_containers(
            self.variables, raise_errors=raise_errors
        )

    def _get_default_plots_to_log(self) -> List[str]:
        return ["forecast", "residuals", "diagnostics"]

    def _check_fh(
        self, fh: Union[List[int], int, np.array, ForecastingHorizon]
    ) -> Union[np.array, ForecastingHorizon]:
        """
        Checks fh for validity and converts fh into an appropriate forecasting
        horizon compatible with sktime (if necessary)

        Parameters
        ----------
        fh : Union[List[int], int, np.array, ForecastingHorizon]
            Forecasting Horizon

        Returns
        -------
        Union[np.array, ForecastingHorizon]
            Forecast Horizon (possibly updated to made compatible with sktime)

        Raises
        ------
        ValueError
            (1) When forecast horizon is an integer < 1
            (2) When forecast horizon is not the correct type
        """
        if isinstance(fh, int):
            if fh >= 1:
                fh = np.arange(1, fh + 1)
            else:
                raise ValueError(
                    f"If Forecast Horizon `fh` is an integer, it must be >= 1. You provided fh = '{fh}'!"
                )
        elif isinstance(fh, List):
            fh = np.array(fh)
        elif isinstance(fh, (np.ndarray, ForecastingHorizon)):
            # Good to go
            pass
        else:
            raise ValueError(
                "Horizon `fh` must be a of type int, list, or numpy array or "
                f"sktime ForecastingHorizon, got object of {type(fh)} type!"
            )
        return fh

    @staticmethod
    def _check_and_clean_data(data: Union[pd.Series, pd.DataFrame]) -> pd.DataFrame:
        """Check that the data is of the correct type (Pandas Series or DataFrame).
        Also cleans the data before coercing it into a dataframe which is used
        internally for all future tasks.

        Parameters
        ----------
        data : Union[pd.Series, pd.DataFrame]
            Input data

        Returns
        -------
        pd.DataFrame
            Checked and Cleaned version of the data

        Raises
        ------
        ValueError
            Raised if data is not of the correct type

        """
        if not isinstance(data, (pd.Series, pd.DataFrame)):
            raise ValueError(
                f"Data must be a pandas Series or DataFrame, got object of {type(data)} type!"
            )

        ## Make a local copy (to perfrom inplace operation on the original dataset)
        data_ = data.copy()

        if isinstance(data_, pd.Series):
            # Set data name is not already set
            data_.name = data_.name if data.name is not None else "Time Series"
            data_ = pd.DataFrame(data_)  # Force convertion to DataFrame

        #### Clean column names ----
        data_.columns = [str(x) for x in data_.columns]

        return data_

    @staticmethod
    def _return_target_names(
        data: pd.DataFrame, target: Optional[Union[str, List[str]]] = None
    ) -> List[str]:

        cols = data.shape[1]

        #### target can not be None if there are multiple columns ----
        if cols > 1 and target is None:
            raise ValueError(
                f"Data has {cols} columns, but the target has not been specified."
            )

        #### Set target if there is only 1 column ----
        if cols == 1:
            if target is not None and target != data.columns[0]:
                raise ValueError(
                    f"Target = '{target}', but data only has '{data.columns[0]}'. "
                    "If you are passing a series (or a dataframe with 1 column) "
                    "to setup, you can leave `target=None`"
                )
            elif target is None:
                # Use the available column
                target = [data.columns[0]]

        if isinstance(target, str):
            # Coerce to list
            target = [target]

        return target

    def _check_and_set_targets(
        self, data: pd.DataFrame, target: Optional[Union[str, List[str]]] = None
    ):
        """Checks that the targets are of correct type and sets class
        attributes related to target(s)

        Parameters
        ----------
        data : pd.DataFrame
            Data from which the targets have to be extracted

        target : Optional[Union[str, List[str]]], default = None
            Target name to be forecasted. Must be specified when data is a pandas
            DataFrame with more than 1 column. When data is a pandas Series or
            pandas DataFrame with 1 column, this can be left as None.

        Raises
        ------
        TypeError
            If the target(s) are not of numeric type
        """

        #### Get Target Name ----
        target = self._return_target_names(data=data, target=target)

        if isinstance(target, list) and len(target) == 1:
            target = target[0]

        if target not in data.columns.to_list():
            raise ValueError(f"Target Column '{target}' is not present in the data.")

        #### Check type of target values - must be numeric ----
        if not np.issubdtype(data[target].dtype, np.number):
            raise TypeError(
                f"Data must be of 'numpy.number' subtype, got {data[target].dtype}!"
            )

        self.target_param = target

    def _check_and_clean_index(
        self,
        data: pd.DataFrame,
        index: Optional[str] = None,
        seasonal_period: Optional[Union[List[Union[int, str]], int, str]] = None,
    ) -> pd.DataFrame:
        """
        Checks if the index is one of the allowed types (pd.PeriodIndex,
        pd.DatetimeIndex). If it is not one of the allowed types, then seasonal
        period must be provided. This check is also performed. Finally, index is
        coerced into period index which is used in subsequent steps and the
        appropriate class for data index is set so that it can be used to disable
        certain models which do not support that type of index.

        Parameters
        ----------
        data : pd.DataFrame
            Data Frame whose index has to be checked and cleaned

        index: Optional[str], default = None
            Column name to be used as the datetime index for modeling. Column is
            internally converted to datetime using `pd.to_datetime()`. If None,
            then the data's index is used as is for modeling.

        seasonal_period : Optional[Union[List[Union[int, str]], int, str]], default = None
            Seasonal Period specified by user

        Returns
        -------
        pd.DataFrame
            Data with checked and cleaned version of the index

        Raises
        ------
        ValueError
            Raised when
            (1) Index has duplicate values.
            (2) Index is not one of the allowed types and seasonal period is not provided
        """

        #### Set Index if necessary ----
        if index is not None:
            if index in data.columns.to_list():
                unique_index_before = len(data[index]) == len(set(data[index]))
                data[index] = pd.to_datetime(data[index])
                unique_index_after = len(data[index]) == len(set(data[index]))
                if unique_index_before and not unique_index_after:
                    raise ValueError(
                        f"Coresion of Index column '{index}' to datetime led to duplicates!"
                        " Consider setting the data index outside pycaret before passing to setup()."
                    )
                data.set_index(index, inplace=True)
            else:
                raise ValueError(
                    f"Index '{index}' is not a column in the data provided."
                )

        #### Data must not have duplicate indices ----
        if len(data.index) != len(set(data.index)):
            raise ValueError("Index may not have duplicate values!")

        #### Check Index Type ----
        allowed_freq_index_types = (pd.PeriodIndex, pd.DatetimeIndex)
        if (
            not isinstance(data.index, allowed_freq_index_types)
            and seasonal_period is None
        ):
            # https://stackoverflow.com/questions/3590165/join-a-list-of-items-with-different-types-as-string-in-python
            raise ValueError(
                f"The index of your 'data' is of type '{type(data.index)}'. "
                "If the 'data' index is not of one of the following types: "
                f"{', '.join(str(type) for type in allowed_freq_index_types)}, "
                "then 'seasonal_period' must be provided. Refer to docstring for options."
            )

        #### Convert DateTimeIndex index to PeriodIndex ----
        # We use PeriodIndex in PyCaret since it seems to be more robust per `sktime``
        # Ref: https://github.com/alan-turing-institute/sktime/blob/v0.10.0/sktime/forecasting/base/_fh.py#L524
        if isinstance(data.index, pd.DatetimeIndex):
            data.index = data.index.to_period()

        #### Save index type so that we can disable certain models ----
        # E.g. Prophet when index if of type RangeIndex
        self.index_type = type(data.index)

        return data

    def _check_and_set_fh(
        self,
        fh: Optional[Union[List[int], int, np.array]],
        fold_strategy: Union[str, Any],
        fold: int,
    ):
        """Checks and sets the forecast horizon class attribute based on the user inputs.
        (1) If fold_strategy is of type string, then fh must be provided
            and is used to set the forecast horizon.
        (2) If fold_strategy is not of type string, then forecast horizon is
            derived from the fold_strategy object's internal fh

        Parameters
        ----------
        fh : Optional[Union[List[int], int, np.array]]
            Forecast Horizon specified by user
        fold_strategy : Union[str, Any]
            Fold Strategy specified by user
        fold : int
            Number of folds specified by user

        Raises
        ------
        ValueError
            fold_strategy is of type string and fh is not provided.
        """

        self.logger.info("Set Forecast Horizon.")

        #### Forecast Horizon Checks ----
        if fh is None:
            if isinstance(fold_strategy, str):
                raise ValueError(
                    "The forecast horizon `fh` must be provided when fold_strategy is of type 'string'"
                )
        elif not isinstance(fh, (int, list, np.ndarray)):
            raise TypeError(
                f"fh parameter accepts integer. list or np.array value. Provided values is {type(fh)}"
            )

        #### Check Fold Strategy ----
        if not isinstance(fold_strategy, str):
            self.logger.info(
                f"fh parameter {fh} will be ignored since fold_strategy has been provided. "
                f"fh from fold_strategy will be used instead."
            )
            fh = fold_strategy.fh
            self.logger.info(
                f"fold parameter '{fold}' will be ignored since fold_strategy has been provided. "
                f"fold based on fold_strategy will be used instead."
            )
            # fold value will be reset after the data is split in the parent class setup

        fh = self._check_fh(fh)
        self.fh = fh

    def _check_and_set_seasonal_period(
        self,
        data: pd.DataFrame,
        seasonal_period: Optional[Union[List[Union[int, str]], int, str]],
    ):
        """Derived the seasonal periods by either
        (1) Extracting it from data's index (if seasonal period is not provided), or
        for each value of seasonal_period:
            (2) Extracting it from the value if it is of type string, or
            (3) Using the value as is if it is of type int.

        After deriving the seasonal periods, a seasonality test is performed for each
        value of seasonal_period. Final seasonal period class attribute value is set equal to
        (1) 1 if seasonality is not detected at any of the derived seasonal periods, or
        (2) the derived seasonal periods for which seasonality is detected.

        Parameters
        ----------
        data : pd.DataFrame
            Data used index can be used to extract the seasonal period information
        seasonal_period : Optional[Union[List[Union[int, str]], int, str]]
            Seasonal Period specified by user

        Raises
        ------
        ValueError
            If seasonal period is provided but its values are not of type int or string
        """
        self.logger.info("Set up Seasonal Period.")

        # sktime is an optional dependency
        from sktime.utils.seasonality import autocorrelation_seasonality_test

        if seasonal_period is None:
            seasonal_period = data.index.freqstr

        if not isinstance(seasonal_period, list):
            seasonal_period = [seasonal_period]
        seasonal_period = [self._convert_sp_to_int(sp) for sp in seasonal_period]

        # check valid seasonal parameter
        # We use y_transformed here instead of y for 2 reasons:
        # (1) Missing values in y will cause issues with this test (seasonality
        #     will not be detected properly).
        # (2) The actual forecaster will see transformed values of y for training.
        #     Hence, these transformed values should be used to determine seasonality.
        seasonality_test_results = [
            autocorrelation_seasonality_test(self.y_transformed, sp)
            for sp in seasonal_period
        ]
        self.seasonality_present = any(seasonality_test_results)
        sp_values_and_test_result = zip(seasonal_period, seasonality_test_results)

        # What seasonal period should be used for modeling?
        self.all_sp_values = [
            sp
            for sp, seasonality_present in sp_values_and_test_result
            if seasonality_present
        ] or [1]
        self.primary_sp_to_use = self.all_sp_values[0]
        self.seasonal_period = (
            seasonal_period[0] if len(seasonal_period) == 1 else seasonal_period
        )

    def _convert_sp_to_int(self, seasonal_period):
        """Derives the seasonal period specified by either:
            (1) Extracting it from the seasonal_period if it is of type string, or
            (2) Using seasonal_period as is if it is of type int.

        Parameters
        ----------
        seasonal_period : Optional[Union[int, str]]
            Seasonal Period specified by user

        Raises
        ------
        ValueError
            If seasonal period is provided but is not of type int or string
        """
        if not isinstance(seasonal_period, (int, str)):
            raise ValueError(
                f"seasonal_period parameter must be an int or str, got {type(seasonal_period)}"
            )

        if isinstance(seasonal_period, str):
            return get_sp_from_str(str_freq=seasonal_period)

        return seasonal_period

    @staticmethod
    def _return_exogenous_names(
        data: pd.DataFrame, target: List[str], ignore_features: Optional[List] = None
    ):

        cols = data.columns.to_list()

        ignore_features = ignore_features if ignore_features is not None else []
        exo_variables = [item for item in cols if item not in ignore_features]

        # Remove targets
        exo_variables = [item for item in exo_variables if item != target]

        return exo_variables

    def _check_and_set_forecsting_types(self):
        """Checks & sets the the forecasting types based on the number of Targets
        and Exogenous Variables.

        Raises
        ------
        ValueError
            If Forecasting type is unsupported (e.g. Multivariate Forecasting)
        """
        #### Univariate or Multivariate ----
        if isinstance(self.target_param, str):
            self.approach_type = TSApproachTypes.UNI
        elif isinstance(self.target_param, list):
            self.approach_type = TSApproachTypes.MULTI
            raise ValueError("Multivariate forecasting is currently not supported")

        #### Data has exogenous variables or not ----
        if len(self.exogenous_variables) > 0:
            self.exogenous_present = TSExogenousPresent.YES
        else:
            self.exogenous_present = TSExogenousPresent.NO

    def setup(
        self,
        data: Union[pd.Series, pd.DataFrame],
        target: Optional[str] = None,
        index: Optional[str] = None,
        ignore_features: Optional[List] = None,
        preprocess: bool = True,
        numeric_imputation_target: Optional[Union[int, float, str]] = None,
        numeric_imputation_exogenous: Optional[Union[int, float, str]] = None,
        transform_target: Optional[str] = None,
        transform_exogenous: Optional[str] = None,
        scale_target: Optional[str] = None,
        scale_exogenous: Optional[str] = None,
        fold_strategy: Union[str, Any] = "expanding",
        fold: int = 3,
        fh: Optional[Union[List[int], int, np.array]] = 1,
        seasonal_period: Optional[Union[List[Union[int, str]], int, str]] = None,
        enforce_pi: bool = False,
        enforce_exogenous: bool = True,
        n_jobs: Optional[int] = -1,
        use_gpu: bool = False,
        custom_pipeline: Union[
            Any, Tuple[str, Any], List[Any], List[Tuple[str, Any]]
        ] = None,
        html: bool = True,
        session_id: Optional[int] = None,
        system_log: Union[bool, logging.Logger] = True,
        log_experiment: bool = False,
        experiment_name: Optional[str] = None,
        experiment_custom_tags: Optional[Dict[str, Any]] = None,
        log_plots: Union[bool, list] = False,
        log_profile: bool = False,
        log_data: bool = False,
        verbose: bool = True,
        profile: bool = False,
        profile_kwargs: Optional[Dict[str, Any]] = None,
        fig_kwargs: Optional[Dict[str, Any]] = None,
    ):
        """
        This function initializes the training environment and creates the transformation
        pipeline. Setup function must be called before executing any other function. It takes
        one mandatory parameters: ``data``. All the other parameters are optional.

        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)


        data : pandas.Series or pandas.DataFrame
            Shape (n_samples, 1), when pandas.DataFrame, otherwise (n_samples, ).


        target : Optional[str], default = None
            Target name to be forecasted. Must be specified when data is a pandas
            DataFrame with more than 1 column. When data is a pandas Series or
            pandas DataFrame with 1 column, this can be left as None.


        index: Optional[str], default = None
            Column name to be used as the datetime index for modeling. Column is
            internally converted to datetime using `pd.to_datetime()`. If None,
            then the data's index is used as is for modeling.


        ignore_features: Optional[List], default = None
            List of features to ignore for modeling when the data is a pandas
            Dataframe with more than 1 column. Ignored when data is a pandas Series
            or Dataframe with 1 column.


        preprocess: bool, default = True
            Should preprocessing be done on the data (includes imputation,
            transformation, scaling)? By default True, but all steps are disabled.
            Enable the steps that need to be preprocessed using appropriate arguments.


        numeric_imputation_target: Optional[Union[int, float, str]], default = None
            Indicates how to impute missing values in the target.
            If None, no imputation is done.
            If the target has missing values, then imputation is mandatory.
            If str, then value passed as is to the underlying `sktime` imputer.
            Allowed values are:
                "drift", "linear", "nearest", "mean", "median", "backfill",
                "bfill", "pad", "ffill", "random"
            If int or float, imputation method is set to "constant" with the given value.


        numeric_imputation_exogenous: Optional[Union[int, float, str]], default = None
            Indicates how to impute missing values in the exogenous variables.
            If None, no imputation is done.
            If exogenous variables have missing values, then imputation is mandatory.
            If str, then value passed as is to the underlying `sktime` imputer.
            Allowed values are:
                "drift", "linear", "nearest", "mean", "median", "backfill",
                "bfill", "pad", "ffill", "random"
            If int or float, imputation method is set to "constant" with the given value.


        transform_target: Optional[str], default = None
            Indicates how the target variable should be transformed.
            If None, no transformation is performed. Allowed values are
                "box-cox", "log", "sqrt", "exp", "cos"


        transform_exogenous: Optional[str], default = None
            Indicates how the exogenous variables should be transformed.
            If None, no transformation is performed. Allowed values are
                "box-cox", "log", "sqrt", "exp", "cos"

        scale_target: Optional[str], default = None
            Indicates how the target variable should be scaled.
            If None, no scaling is performed. Allowed values are
                "zscore", "minmax", "maxabs", "robust"

        scale_exogenous: Optional[str], default = None
            Indicates how the exogenous variables should be scaled.
            If None, no scaling is performed. Allowed values are
                "zscore", "minmax", "maxabs", "robust"


        fold_strategy: str or sklearn CV generator object, default = 'expanding'
            Choice of cross validation strategy. Possible values are:

            * 'expanding'
            * 'rolling' (same as/aliased to 'expanding')
            * 'sliding'

            You can also pass an sktime compatible cross validation object such
            as ``SlidingWindowSplitter`` or ``ExpandingWindowSplitter``. In this case,
            the `fold` and `fh` parameters will be ignored and these values will
            be extracted from the ``fold_strategy`` object directly.


        fold: int, default = 3
            Number of folds to be used in cross validation. Must be at least 2. This is
            a global setting that can be over-written at function level by using ``fold``
            parameter. Ignored when ``fold_strategy`` is a custom object.


        fh: Optional[int or list or np.array], default = 1
            The forecast horizon to be used for forecasting. Default is set to ``1`` i.e.
            forecast one point ahead. When integer is passed it means N continuous points
            in the future without any gap. If you want to forecast values with gaps, you
            must pass an array e.g. np.arange([13, 25]) will skip the first 12 future
            points and forecast from the 13th point till the 24th point ahead (note in
            numpy right value is inclusive and left is exclusive).

            If fh = None, then fold_strategy must be a sktime compatible cross validation
            object. In this case, fh is derived from this object.


        seasonal_period: list or int or str, default = None
            Seasonal period in timeseries data. If not provided the frequency of the data
            index is mapped to a seasonal period as follows:

            * 'S': 60
            * 'T': 60
            * 'H': 24
            * 'D': 7
            * 'W': 52
            * 'M': 12
            * 'Q': 4
            * 'A': 1
            * 'Y': 1

            Alternatively you can provide a custom `seasonal_period` by passing
            it as an integer or a string corresponding to the keys above (e.g.
            'W' for weekly data, 'M' for monthly data, etc.). You can also provide
            a list of such values to use in models that accept multiple seasonal values
            (currently TBATS). For models that don't accept multiple seasonal values, the
            first value of the list will be used as the seasonal period.


        enforce_pi: bool, default = False
            When set to True, only models that support prediction intervals are
            loaded in the environment.


        enforce_exogenous: bool, default = True
            When set to True and the data includes exogenous variables, only models
            that support exogenous variables are loaded in the environment.When
            set to False, all models are included and in this case, models that do
            not support exogenous variables will model the data as a univariate
            forecasting problem.


        n_jobs: int, default = -1
            The number of jobs to run in parallel (for functions that supports parallel
            processing) -1 means using all processors. To run all functions on single
            processor set n_jobs to None.


        use_gpu: bool or str, default = False
            Parameter not in use for now. Behavior may change in future.


        custom_pipeline: (str, transformer) or list of (str, transformer), default = None
            Parameter not in use for now. Behavior may change in future.


        html: bool, default = True
            When set to False, prevents runtime display of monitor. This must be set to False
            when the environment does not support IPython. For example, command line terminal,
            Databricks Notebook, Spyder and other similar IDEs.


        session_id: int, default = None
            Controls the randomness of experiment. It is equivalent to 'random_state' in
            scikit-learn. When None, a pseudo random number is generated. This can be used
            for later reproducibility of the entire experiment.


        system_log: bool or logging.Logger, default = True
            Whether to save the system logging file (as logs.log). If the input already is a
            logger object, that one is used instead.


        log_experiment: bool, default = False
            When set to True, all metrics and parameters are logged on the ``MLflow`` server.


        experiment_name: str, default = None
            Name of the experiment for logging. Ignored when ``log_experiment`` is not True.


        log_plots: bool or list, default = False
            When set to True, certain plots are logged automatically in the ``MLFlow`` server.
            To change the type of plots to be logged, pass a list containing plot IDs. Refer
            to documentation of ``plot_model``. Ignored when ``log_experiment`` is not True.


        log_profile: bool, default = False
            When set to True, data profile is logged on the ``MLflow`` server as a html file.
            Ignored when ``log_experiment`` is not True.


        log_data: bool, default = False
            When set to True, dataset is logged on the ``MLflow`` server as a csv file.
            Ignored when ``log_experiment`` is not True.


        verbose: bool, default = True
            When set to False, Information grid is not printed.


        profile: bool, default = False
            When set to True, an interactive EDA report is displayed.


        profile_kwargs: dict, default = {} (empty dict)
            Dictionary of arguments passed to the ProfileReport method used
            to create the EDA report. Ignored if ``profile`` is False.


        fig_kwargs: dict, default = {} (empty dict)
            The global setting for any plots. Pass these as key-value pairs.
            Example: fig_kwargs = {"height": 1000, "template": "simple_white"}

            Available keys are:

            hoverinfo: hoverinfo passed to Plotly figures. Can be any value supported
                by Plotly (e.g. "text" to display, "skip" or "none" to disable.).
                When not provided, hovering over certain plots may be disabled by
                PyCaret when the data exceeds a  certain number of points (determined
                by `big_data_threshold`).

            renderer: The renderer used to display the plotly figure. Can be any value
                supported by Plotly (e.g. "notebook", "png", "svg", etc.). Note that certain
                renderers (like "svg") may need additional libraries to be installed. Users
                will have to do this manually since they don't come preinstalled wit plotly.
                When not provided, plots use plotly's default render when data is below a
                certain number of points (determined by `big_data_threshold`) otherwise it
                switches to a static "png" renderer.

            template: The template to use for the plots. Can be any value supported by Plotly.
                If not provided, defaults to "ggplot2"

            width: The width of the plot in pixels. If not provided, defaults to None
                which lets Plotly decide the width.

            height: The height of the plot in pixels. If not provided, defaults to None
                which lets Plotly decide the height.

            rows: The number of rows to use for plots where this can be customized,
                e.g. `ccf`. If not provided, defaults to None which lets PyCaret decide
                based on number of subplots to be plotted.

            cols: The number of columns to use for plots where this can be customized,
                e.g. `ccf`. If not provided, defaults to 4

            big_data_threshold: The number of data points above which hovering over
                certain plots can be disabled and/or renderer switched to a static
                renderer. This is useful when the time series being modeled has a lot
                of data which can make notebooks slow to render.

        Returns:
            Global variables that can be changed using the ``set_config`` function.


        """
        # sktime is an optional dependency
        from sktime.forecasting.model_selection import temporal_train_test_split

        ##############################
        #### Setup initialization ####
        ##############################

        runtime_start = time.time()

        #### Define parameter attrs ----
        self.fig_kwargs = fig_kwargs or {}
        self._set_default_fig_kwargs()

        self.enforce_pi = enforce_pi
        self.enforce_exogenous = enforce_exogenous
        self.preprocess = preprocess
        # self.imputation_type = imputation_type
        self.log_plots_param = log_plots

        # Needed for compatibility with Regression and Classification.
        # Not used in Time Series
        self.fold_groups_param = None
        self.fold_groups_param_full = None
        self.transform_target_param = None

        # Features to be ignored (are not read by self.dataset, self.X, etc...)
        self._fxs = {"Ignore": ignore_features or []}

        ## Make a local copy (to perfrom inplace operation on the original dataset)
        data_ = data.copy()

        # TODO: What is memory and is it needed as an argument to the setup?
        # https://github.com/pycaret/pycaret/blob/eb3a958ea159c600eb8d536cb902d309aa18ebcb/pycaret/internal/pycaret_experiment/class_reg_experiment.py#L138
        memory: Union[bool, str, Memory] = True

        self._initialize_setup(
            n_jobs=n_jobs,
            use_gpu=use_gpu,
            html=html,
            session_id=session_id,
            system_log=system_log,
            log_experiment=log_experiment,
            experiment_name=experiment_name,
            memory=memory,
            verbose=verbose,
        )

        #### Check and Clean Data ----
        data_ = self._check_and_clean_data(data)

        #### Check and Clean Index ----
        data_ = self._check_and_clean_index(
            data=data_, index=index, seasonal_period=seasonal_period
        )

        #### Check and Set Targets ----
        self._check_and_set_targets(data=data_, target=target)

        #### Check and Set Exogenous Variables ----
        self.exogenous_variables = self._return_exogenous_names(
            data=data_, target=self.target_param, ignore_features=ignore_features
        )

        #### Set type of forecasting ----
        self._check_and_set_forecsting_types()

        #### Set Forecast Horizon ----
        self._check_and_set_fh(fh=fh, fold_strategy=fold_strategy, fold=fold)

        ###############################
        #### Set Train Test Splits ####
        ###############################

        self.logger.info("Set up Train-Test Splits.")

        # If `fh` is provided it splits by it
        y = data_[self.target_param]
        X = data_.drop(self.target_param, axis=1)

        y_train, y_test, X_train, X_test = temporal_train_test_split(
            y=y, X=X, fh=self.fh
        )

        # Coerce y into a dataframes ----
        # TODO: Is this needed here since we are not explicitly setting y_train and y_test?
        y_train, y_test = (
            pd.DataFrame(y_train),
            pd.DataFrame(y_test),
        )

        self.data = data_
        # idx contains train, test indices.
        # Setting of self.y_train, self.y_test, self.X_train and self.X_test
        # will be handled internally based on these indices and self.data
        self.idx = [y_train.index, y_test.index]

        ######################################
        #### Setup Cross Validation Folds ####
        ######################################

        possible_time_series_fold_strategies = ["expanding", "sliding", "rolling"]
        #### TODO: Change is_sklearn_cv_generator to check for sktime instead
        if not (
            fold_strategy in possible_time_series_fold_strategies
            or is_sklearn_cv_generator(fold_strategy)
        ):
            raise TypeError(
                "fold_strategy parameter must be either a sktime compatible CV generator "
                f"object or one of '{', '.join(possible_time_series_fold_strategies)}'."
            )

        if fold_strategy in possible_time_series_fold_strategies:
            self.fold_strategy = fold_strategy  # save for use in methods later
            # Number of folds
            self.fold_param = fold
            self.fold_generator = self.get_fold_generator(fold=self.fold_param)
        else:
            # TODO: Add the correct self.fold_strategy here based type of fold_strategy
            self.fold_generator = fold_strategy
            # Number of folds
            self.fold_param = fold_strategy.get_n_splits(y=self.y_train)

        # Preprocessing ============================================ >>

        num_missing_target = self.y.isna().sum()
        self.target_has_missing = num_missing_target != 0
        if isinstance(self.X, pd.DataFrame):
            num_missing_exogenous = self.X.isna().sum().sum()
            self.exogenous_has_missing = num_missing_exogenous != 0
        elif self.X is None:
            num_missing_exogenous = 0
            self.exogenous_has_missing = False

        if self.target_has_missing and numeric_imputation_target is None:
            raise ValueError(
                "\nTime Series modeling automation relies on running statistical tests, plots, etc.\n"
                "Many of these can not be run when data has missing values. \nYour target has "
                f"{num_missing_target} missing values and `numeric_imputation_target` is set to "
                "`None`. \nPlease enable imputation to proceed. "
            )
        if self.exogenous_has_missing and numeric_imputation_exogenous is None:
            raise ValueError(
                "\nTime Series modeling automation relies on running statistical tests, plots, etc.\n"
                "Many of these can not be run when data has missing values. \nYour exogenous data "
                f"has {num_missing_exogenous} missing values and `numeric_imputation_exogenous` is "
                "set to `None`. \nPlease enable imputation to proceed. "
            )

        # Initialize empty steps ----
        self.pipe_steps_target = []
        self.pipe_steps_exogenous = []

        if preprocess:
            self.logger.info("Preparing preprocessing pipeline...")

            #### Impute missing values ----
            if numeric_imputation_target is not None:
                self._imputation(
                    numeric_imputation=numeric_imputation_target, target=True
                )
            # Only add exogenous pipeline steps if exogenous variables are present.
            if (
                self.exogenous_present == TSExogenousPresent.YES
                and numeric_imputation_exogenous is not None
            ):
                self._imputation(
                    numeric_imputation=numeric_imputation_exogenous, target=False
                )

            # Transformations (preferably based on residual analysis) ----
            if transform_target is not None:
                self._transformation(transform=transform_target, target=True)
            # Only add exogenous pipeline steps if exogenous variables are present.
            if (
                self.exogenous_present == TSExogenousPresent.YES
                and transform_exogenous is not None
            ):
                self._transformation(transform=transform_exogenous, target=False)

            # Scaling ----
            if scale_target:
                self._scaling(scale=scale_target, target=True)
            # Only add exogenous pipeline steps if exogenous variables are present.
            if (
                self.exogenous_present == TSExogenousPresent.YES
                and scale_exogenous is not None
            ):
                self._scaling(scale=scale_exogenous, target=False)

        # # Add custom transformers to the pipeline
        # if custom_pipeline:
        #     self._add_custom_pipeline(custom_pipeline)

        self.pipeline = self._create_pipeline(
            dummy_model=DummyForecaster(),
            target_steps=self.pipe_steps_target,
            exogenous_steps=self.pipe_steps_exogenous,
        )

        self.pipeline.fit(y=self.y_train, X=self.X_train, fh=self.fh)

        self.logger.info("Finished creating preprocessing pipeline.")
        self.logger.info(f"Pipeline: {self.pipeline}")

        ##################################################################
        #### Do these after the preprocessing pipeline has been setup ####
        ##################################################################
        # Since the models will see transformed data, these parameters should
        # also be derived from the transformed data.

        #### Set up Seasonal Period ----
        self._check_and_set_seasonal_period(data=data_, seasonal_period=seasonal_period)

        #### Multiplicative components allowed? ----
        self.logger.info("Set up whether Multiplicative components allowed.")
        # Should multiplicative components be allowed in models that support it
        self.strictly_positive = np.all(self.y_transformed > 0)

        ############################################
        #### Initial EDA in Setup (for display) ####
        ############################################

        from pycaret.internal.tests.time_series import (
            recommend_uppercase_d,
            recommend_lowercase_d,
        )

        self.white_noise = None
        wn_results = self.check_stats(test="white_noise")
        wn_values = wn_results.query("Property == 'White Noise'")["Value"]

        # There can be multiple lags values tested.
        # Checking the percent of lag values that indicate white noise
        percent_white_noise = sum(wn_values) / len(wn_values)
        if percent_white_noise == 0:
            self.white_noise = "No"
        elif percent_white_noise == 1.00:
            self.white_noise = "Yes"
        else:
            self.white_noise = "Maybe"

        # We use y_transformed here instead of y for 2 reasons:
        # (1) Missing values in y will cause issues with this test.
        # (2) The actual forecaster will see transformed values of y for training.
        #     Hence d, and D should be computed using the transformed values.
        self.lowercase_d = recommend_lowercase_d(data=self.y_transformed)
        if self.primary_sp_to_use > 1:
            try:
                max_D = 2
                uppercase_d = recommend_uppercase_d(
                    data=self.y_transformed, sp=self.primary_sp_to_use, max_D=max_D
                )
            except ValueError:
                self.logger.info("Test for computing 'D' failed at max_D = 2.")
                try:
                    max_D = 1
                    uppercase_d = recommend_uppercase_d(
                        data=self.y_transformed, sp=self.primary_sp_to_use, max_D=max_D
                    )
                except ValueError:
                    self.logger.info("Test for computing 'D' failed at max_D = 1.")
                    uppercase_d = 0
        else:
            uppercase_d = 0
        self.uppercase_d = uppercase_d

        #######################
        #### Final display ####
        #######################

        self.logger.info("Creating final display dataframe.")
        self.display_container = [self._get_setup_display()]
        self.logger.info(f"Setup Display Container: {self.display_container[0]}")
        if self.verbose:
            pd.set_option("display.max_rows", 100)
            print(self.display_container[0].style.apply(highlight_setup))
            pd.reset_option("display.max_rows")  # Reset option

        #################
        #### Wrap-up ####
        #################

        # Create a profile report
        self._profile(profile, profile_kwargs)

        # Define models and metrics
        self._all_models, self._all_models_internal = self._get_models()
        self._all_metrics = self._get_metrics()

        runtime = np.array(time.time() - runtime_start).round(2)

        self._set_up_mlflow(
            runtime,
            log_data,
            log_profile,
            experiment_custom_tags=experiment_custom_tags,
        )

        self._setup_ran = True

        #############################
        #### Cleanup after Setup ####
        #############################

        #### Disabling of certain metrics. ----
        ## NOTE: This must be run after _setup_ran has been set, else metrics can
        # not be retrieved.

        #### Disable R2 when fh = 1 ----
        if len(self.fh) == 1 and "r2" in self._get_metrics():
            # disable R2 metric if it exists in the metrics since R2 needs
            # at least 2 values
            self.remove_metric("R2")

        #### Remove COVERAGE when enforce_pi is False ----
        # User can add it manually if they want when enforce_pi is set to False.
        # Refer: https://github.com/pycaret/pycaret/issues/1900
        if not self.enforce_pi and "coverage" in self._get_metrics():
            self.remove_metric("COVERAGE")

        self.logger.info(f"setup() successfully completed in {runtime}s...............")

        return self

    def _set_default_fig_kwargs(self):
        """Set the default values for `fig_kwargs` if these are not provided by the user."""

        # `big_data_threshold`: Number of data points above which the hovering for
        # some plots is disabled. This is needed else the notebooks become very slow.
        defaults = {
            "big_data_threshold": 200,
            "hoverinfo": None,
            "renderer": None,
            "template": "ggplot2",
            "rows": None,
            "cols": 4,
            "width": None,
            "height": None,
        }

        # Set to default if missing ----
        for key in defaults:
            self.fig_kwargs[key] = self.fig_kwargs.get(key, defaults[key])

    def compare_models(
        self,
        include: Optional[List[Union[str, Any]]] = None,
        exclude: Optional[List[str]] = None,
        fold: Optional[Union[int, Any]] = None,
        round: int = 4,
        cross_validation: bool = True,
        sort: str = "smape",
        n_select: int = 1,
        budget_time: Optional[float] = None,
        turbo: bool = True,
        errors: str = "ignore",
        fit_kwargs: Optional[dict] = None,
        experiment_custom_tags: Optional[Dict[str, Any]] = None,
        verbose: bool = True,
    ):

        """
        This function trains and evaluates performance of all estimators available in the
        model library using cross validation. The output of this function is a score grid
        with average cross validated scores. Metrics evaluated during CV can be accessed
        using the ``get_metrics`` function. Custom metrics can be added or removed using
        ``add_metric`` and ``remove_metric`` function.


        Example
        --------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)
        >>> best_model = compare_models()


        include: list of str or sktime compatible object, default = None
            To train and evaluate select models, list containing model ID or scikit-learn
            compatible object can be passed in include param. To see a list of all models
            available in the model library use the ``models`` function.


        exclude: list of str, default = None
            To omit certain models from training and evaluation, pass a list containing
            model id in the exclude parameter. To see a list of all models available
            in the model library use the ``models`` function.


        fold: int or scikit-learn compatible CV generator, default = None
            Controls cross-validation. If None, the CV generator in the ``fold_strategy``
            parameter of the ``setup`` function is used. When an integer is passed,
            it is interpreted as the 'n_splits' parameter of the CV generator in the
            ``setup`` function.


        round: int, default = 4
            Number of decimal places the metrics in the score grid will be rounded to.


        cross_validation: bool, default = True
            When set to False, metrics are evaluated on holdout set. ``fold`` param
            is ignored when cross_validation is set to False.


        sort: str, default = 'SMAPE'
            The sort order of the score grid. It also accepts custom metrics that are
            added through the ``add_metric`` function.


        n_select: int, default = 1
            Number of top_n models to return. For example, to select top 3 models use
            n_select = 3.


        budget_time: int or float, default = None
            If not None, will terminate execution of the function after budget_time
            minutes have passed and return results up to that point.


        turbo: bool, default = True
            When set to True, it excludes estimators with longer training times. To
            see which algorithms are excluded use the ``models`` function.


        errors: str, default = 'ignore'
            When set to 'ignore', will skip the model with exceptions and continue.
            If 'raise', will break the function when exceptions are raised.


        fit_kwargs: dict, default = {} (empty dict)
            Dictionary of arguments passed to the fit method of the model.


        verbose: bool, default = True
            Score grid is not printed when verbose is set to False.


        Returns:
            Trained model or list of trained models, depending on the ``n_select`` param.


        Warnings
        --------
        - Changing turbo parameter to False may result in very high training times.

        - No models are logged in ``MLflow`` when ``cross_validation`` parameter is False.

        """

        return super().compare_models(
            include=include,
            exclude=exclude,
            fold=fold,
            round=round,
            cross_validation=cross_validation,
            sort=sort,
            n_select=n_select,
            budget_time=budget_time,
            turbo=turbo,
            errors=errors,
            fit_kwargs=fit_kwargs,
            experiment_custom_tags=experiment_custom_tags,
            verbose=verbose,
        )

    def create_model(
        self,
        estimator: Union[str, Any],
        fold: Optional[Union[int, Any]] = None,
        round: int = 4,
        cross_validation: bool = True,
        fit_kwargs: Optional[dict] = None,
        experiment_custom_tags: Optional[Dict[str, Any]] = None,
        verbose: bool = True,
        **kwargs,
    ):

        """
        This function trains and evaluates the performance of a given estimator
        using cross validation. The output of this function is a score grid with
        CV scores by fold. Metrics evaluated during CV can be accessed using the
        ``get_metrics`` function. Custom metrics can be added or removed using
        ``add_metric`` and ``remove_metric`` function. All the available models
        can be accessed using the ``models`` function.


        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)
        >>> naive = create_model('naive')

        estimator: str or sktime compatible object
            ID of an estimator available in model library or pass an untrained
            model object consistent with scikit-learn API. Estimators available
            in the model library (ID - Name):

            * 'naive' - Naive Forecaster
            * 'grand_means' - Grand Means Forecaster
            * 'snaive' - Seasonal Naive Forecaster (disabled when seasonal_period = 1)
            * 'polytrend' - Polynomial Trend Forecaster
            * 'arima' - ARIMA family of models (ARIMA, SARIMA, SARIMAX)
            * 'auto_arima' - Auto ARIMA
            * 'arima' - ARIMA
            * 'exp_smooth' - Exponential Smoothing
            * 'ets' - ETS
            * 'theta' - Theta Forecaster
            * 'tbats' - TBATS
            * 'bats' - BATS
            * 'prophet' - Prophet Forecaster
            * 'lr_cds_dt' - Linear w/ Cond. Deseasonalize & Detrending
            * 'en_cds_dt' - Elastic Net w/ Cond. Deseasonalize & Detrending
            * 'ridge_cds_dt' - Ridge w/ Cond. Deseasonalize & Detrending
            * 'lasso_cds_dt' - Lasso w/ Cond. Deseasonalize & Detrending
            * 'lar_cds_dt' -   Least Angular Regressor w/ Cond. Deseasonalize & Detrending
            * 'llar_cds_dt' - Lasso Least Angular Regressor w/ Cond. Deseasonalize & Detrending
            * 'br_cds_dt' - Bayesian Ridge w/ Cond. Deseasonalize & Deseasonalize & Detrending
            * 'huber_cds_dt' - Huber w/ Cond. Deseasonalize & Detrending
            * 'par_cds_dt' - Passive Aggressive w/ Cond. Deseasonalize & Detrending
            * 'omp_cds_dt' - Orthogonal Matching Pursuit w/ Cond. Deseasonalize & Detrending
            * 'knn_cds_dt' - K Neighbors w/ Cond. Deseasonalize & Detrending
            * 'dt_cds_dt' - Decision Tree w/ Cond. Deseasonalize & Detrending
            * 'rf_cds_dt' - Random Forest w/ Cond. Deseasonalize & Detrending
            * 'et_cds_dt' - Extra Trees w/ Cond. Deseasonalize & Detrending
            * 'gbr_cds_dt' - Gradient Boosting w/ Cond. Deseasonalize & Detrending
            * 'ada_cds_dt' - AdaBoost w/ Cond. Deseasonalize & Detrending
            * 'lightgbm_cds_dt' - Light Gradient Boosting w/ Cond. Deseasonalize & Detrending


        fold: int or scikit-learn compatible CV generator, default = None
            Controls cross-validation. If None, the CV generator in the ``fold_strategy``
            parameter of the ``setup`` function is used. When an integer is passed,
            it is interpreted as the 'n_splits' parameter of the CV generator in the
            ``setup`` function.


        round: int, default = 4
            Number of decimal places the metrics in the score grid will be rounded to.


        cross_validation: bool, default = True
            When set to False, metrics are evaluated on holdout set. ``fold`` param
            is ignored when cross_validation is set to False.


        fit_kwargs: dict, default = {} (empty dict)
            Dictionary of arguments passed to the fit method of the model.


        verbose: bool, default = True
            Score grid is not printed when verbose is set to False.


        **kwargs:
            Additional keyword arguments to pass to the estimator.


        Returns:
            Trained Model


        Warnings
        --------
        - Models are not logged on the ``MLFlow`` server when ``cross_validation`` param
        is set to False.

        """
        return super().create_model(
            estimator=estimator,
            fold=fold,
            round=round,
            cross_validation=cross_validation,
            fit_kwargs=fit_kwargs,
            experiment_custom_tags=experiment_custom_tags,
            verbose=verbose,
            **kwargs,
        )

    @staticmethod
    def update_fit_kwargs_with_fh_from_cv(fit_kwargs: Optional[Dict], cv) -> Dict:
        """Updated the fit_ kwargs to include the fh parameter from cv

        Parameters
        ----------
        fit_kwargs : Optional[Dict]
            Original fit kwargs
        cv : [type]
            cross validation object

        Returns
        -------
        Dict[Any]
            Updated fit kwargs
        """
        fh_param = {"fh": cv.fh}
        if fit_kwargs is None:
            fit_kwargs = fh_param
        else:
            fit_kwargs.update(fh_param)
        return fit_kwargs

    def _get_final_model_from_pipeline(
        self, pipeline: PyCaretForecastingPipeline, check_is_fitted: bool = False
    ) -> BaseForecaster:
        """Extracts and returns the final trained model from the pipeline.

        Parameters
        ----------
        pipeline : PyCaretForecastingPipeline
            The trained pipeline with the correct final model
        check_is_fitted : bool
            If True, will check if final model is fitted and raise an exception
            if it is not.

        Returns
        -------
        BaseForecaster
            The trained final model in the pipeline
        """
        # Pipeline will always be of type PyCaretForecastingPipeline with final
        # forecaster being of type TransformedTargetForecaster
        final_forecaster_only = pipeline.steps_[-1][1].steps_[-1][1]
        if check_is_fitted:
            final_forecaster_only.check_is_fitted()

        return final_forecaster_only

    def _create_model_without_cv(
        self, model, data_X, data_y, fit_kwargs, predict, system, display: Display
    ):
        # fit_kwargs = get_pipeline_fit_kwargs(model, fit_kwargs)
        self.logger.info("Cross validation set to False")

        self.logger.info("Fitting Model")
        model_fit_start = time.time()

        ###############################################
        #### Add the correct model to the pipeline ####
        ###############################################
        pipeline_with_model = _add_model_to_pipeline(
            pipeline=self.pipeline, model=model
        )

        with io.capture_output():
            pipeline_with_model.fit(data_y, data_X, **fit_kwargs)
        model_fit_end = time.time()

        model_fit_time = np.array(model_fit_end - model_fit_start).round(2)

        display.move_progress()

        if predict:
            # X is not passed here so predict_model picks X_test by default.
            self.predict_model(pipeline_with_model, verbose=False)
            model_results = self.pull(pop=True).drop("Model", axis=1)

            self.display_container.append(model_results)

            display.display(
                model_results, clear=system, override=False if not system else None
            )

            self.logger.info(f"display_container: {len(self.display_container)}")

        return pipeline_with_model, model_fit_time

    def _create_model_with_cv(
        self,
        model,
        data_X,
        data_y,
        fit_kwargs,
        round,
        cv,
        groups,  # TODO: See if we can remove groups
        metrics,
        refit,
        system,
        display,
    ):
        """
        MONITOR UPDATE STARTS
        """

        display.update_monitor(1, f"Fitting {cv.get_n_splits(data_y)} Folds")
        display.display_monitor()
        """
        MONITOR UPDATE ENDS
        """
        metrics_dict = {k: v.scorer for k, v in metrics.items()}

        self.logger.info("Starting cross validation")

        n_jobs = self._gpu_n_jobs_param

        self.logger.info(f"Cross validating with {cv}, n_jobs={n_jobs}")

        # Cross Validate time series
        fit_kwargs = self.update_fit_kwargs_with_fh_from_cv(
            fit_kwargs=fit_kwargs, cv=cv
        )

        model_fit_start = time.time()

        additional_scorer_kwargs = self.get_additional_scorer_kwargs()

        ###############################################
        #### Add the correct model to the pipeline ####
        ###############################################
        pipeline_with_model = _add_model_to_pipeline(
            pipeline=self.pipeline, model=model
        )

        scores, cutoffs = cross_validate(
            forecaster=pipeline_with_model,
            y=data_y,
            X=data_X,
            scoring=metrics_dict,
            cv=cv,
            n_jobs=n_jobs,
            verbose=0,
            fit_params=fit_kwargs,
            return_train_score=False,
            error_score=0,
            **additional_scorer_kwargs,
        )

        model_fit_end = time.time()
        model_fit_time = np.array(model_fit_end - model_fit_start).round(2)

        # Scores has metric names in lowercase, scores_dict has metric names in uppercase
        score_dict = {v.display_name: scores[f"{k}"] for k, v in metrics.items()}

        self.logger.info("Calculating mean and std")

        try:
            avgs_dict = {k: [np.mean(v), np.std(v)] for k, v in score_dict.items()}
        except TypeError:
            # When there is an error in model creation, score_dict values are None.
            # e.g.
            #   {
            #       'MAE': [None, None, None],
            #       'RMSE': [None, None, None],
            #       'MAPE': [None, None, None],
            #       'SMAPE': [None, None, None],
            #       'R2': [None, None, None]
            #   }
            # Hence, mean and sd can not be computed
            # TypeError: unsupported operand type(s) for +: 'NoneType' and 'NoneType'
            avgs_dict = {k: [np.nan, np.nan] for k, v in score_dict.items()}

        display.move_progress()

        self.logger.info("Creating metrics dataframe")

        model_results = pd.DataFrame(score_dict)
        model_results.insert(0, "cutoff", cutoffs)

        model_avgs = pd.DataFrame(avgs_dict, index=["Mean", "SD"])
        model_avgs.insert(0, "cutoff", np.nan)

        model_results = model_results.append(model_avgs)
        # Round the results
        model_results = model_results.round(round)

        # yellow the mean (converts model_results from dataframe to dataframe styler)
        model_results = color_df(model_results, "yellow", ["Mean"], axis=1)
        model_results = model_results.set_precision(round)

        if refit:
            # refitting the model on complete X_train, y_train
            display.update_monitor(1, "Finalizing Model")
            display.display_monitor()
            model_fit_start = time.time()
            self.logger.info("Finalizing model")
            with io.capture_output():
                pipeline_with_model.fit(y=data_y, X=data_X, **fit_kwargs)
            model_fit_end = time.time()
            model_fit_time = np.array(model_fit_end - model_fit_start).round(2)
        else:
            model_fit_time /= cv.get_n_splits(data_y)

        #### Return the final model only. Rest of the pipeline will be added during finalize.
        final_model = self._get_final_model_from_pipeline(
            pipeline=pipeline_with_model, check_is_fitted=refit
        )
        return final_model, model_fit_time, model_results, avgs_dict

    def tune_model(
        self,
        estimator,
        fold: Optional[Union[int, Any]] = None,
        round: int = 4,
        n_iter: int = 10,
        custom_grid: Optional[Union[Dict[str, list], Any]] = None,
        optimize: str = "SMAPE",
        custom_scorer=None,
        search_algorithm: Optional[str] = None,
        choose_better: bool = True,
        fit_kwargs: Optional[dict] = None,
        return_tuner: bool = False,
        verbose: bool = True,
        tuner_verbose: Union[int, bool] = True,
        display: Optional[Display] = None,
        **kwargs,
    ):

        """
        This function tunes the hyperparameters of a given estimator. The output of
        this function is a score grid with CV scores by fold of the best selected
        model based on ``optimize`` parameter. Metrics evaluated during CV can be
        accessed using the ``get_metrics`` function. Custom metrics can be added
        or removed using ``add_metric`` and ``remove_metric`` function.


        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)
        >>> dt = create_model('dt_cds_dt')
        >>> tuned_dt = tune_model(dt)


        estimator: sktime compatible object
            Trained model object


        fold: int or scikit-learn compatible CV generator, default = None
            Controls cross-validation. If None, the CV generator in the ``fold_strategy``
            parameter of the ``setup`` function is used. When an integer is passed,
            it is interpreted as the 'n_splits' parameter of the CV generator in the
            ``setup`` function.


        round: int, default = 4
            Number of decimal places the metrics in the score grid will be rounded to.


        n_iter: int, default = 10
            Number of iterations in the grid search. Increasing 'n_iter' may improve
            model performance but also increases the training time.


        custom_grid: dictionary, default = None
            To define custom search space for hyperparameters, pass a dictionary with
            parameter name and values to be iterated. Custom grids must be in a format
            supported by the defined ``search_library``.


        optimize: str, default = 'SMAPE'
            Metric name to be evaluated for hyperparameter tuning. It also accepts custom
            metrics that are added through the ``add_metric`` function.


        custom_scorer: object, default = None
            custom scoring strategy can be passed to tune hyperparameters of the model.
            It must be created using ``sklearn.make_scorer``. It is equivalent of adding
            custom metric using the ``add_metric`` function and passing the name of the
            custom metric in the ``optimize`` parameter.
            Will be deprecated in future.


        search_algorithm: str, default = 'random'
            use 'random' for random grid search and 'grid' for complete grid search.


        choose_better: bool, default = True
            When set to True, the returned object is always better performing. The
            metric used for comparison is defined by the ``optimize`` parameter.


        fit_kwargs: dict, default = {} (empty dict)
            Dictionary of arguments passed to the fit method of the tuner.


        return_tuner: bool, default = False
            When set to True, will return a tuple of (model, tuner_object).


        verbose: bool, default = True
            Score grid is not printed when verbose is set to False.


        tuner_verbose: bool or in, default = True
            If True or above 0, will print messages from the tuner. Higher values
            print more messages. Ignored when ``verbose`` param is False.


        **kwargs:
            Additional keyword arguments to pass to the optimizer.


        Returns:
            Trained Model and Optional Tuner Object when ``return_tuner`` is True.

        """

        search_library = "pycaret"  # only 1 library supported right now

        _allowed_search_algorithms = []
        if search_library == "pycaret":
            _allowed_search_algorithms = [None, "random", "grid"]
            if search_algorithm not in _allowed_search_algorithms:
                raise ValueError(
                    "`search_algorithm` must be one of "
                    f"'{', '.join(str(allowed_type) for allowed_type in _allowed_search_algorithms)}'. "
                    f"You passed '{search_algorithm}'."
                )

        function_params_str = ", ".join([f"{k}={v}" for k, v in locals().items()])

        self.logger.info("Initializing tune_model()")
        self.logger.info(f"tune_model({function_params_str})")

        self.logger.info("Checking exceptions")

        # run_time
        runtime_start = time.time()

        if not fit_kwargs:
            fit_kwargs = {}

        # checking estimator if string
        if type(estimator) is str:
            raise TypeError(
                "The behavior of tune_model in version 1.0.1 is changed. Please pass trained model object."
            )

        # Check for estimator
        if not hasattr(estimator, "fit"):
            raise ValueError(
                f"Estimator {estimator} does not have the required fit() method."
            )

        # checking fold parameter
        if fold is not None and not (
            type(fold) is int or is_sklearn_cv_generator(fold)
        ):
            raise TypeError(
                "fold parameter must be either None, an integer or a scikit-learn compatible CV generator object."
            )

        # checking round parameter
        if type(round) is not int:
            raise TypeError("Round parameter only accepts integer value.")

        # checking n_iter parameter
        if type(n_iter) is not int:
            raise TypeError("n_iter parameter only accepts integer value.")

        if isinstance(optimize, str):
            # checking optimize parameter
            # TODO: Changed with reference to other ML Usecases. Check with Antoni
            # optimize = self._get_metric_by_name_or_id(optimize)
            # if optimize is None:
            #     raise ValueError(
            #         "Optimize method not supported. See docstring for list of available parameters."
            #     )
            optimize_container = self._get_metric_by_name_or_id(optimize)
            if optimize_container is None:
                raise ValueError(
                    "Optimize method not supported. See docstring for list of available parameters."
                )
        else:
            self.logger.info(f"optimize set to user defined function {optimize}")

        # checking verbose parameter
        if type(verbose) is not bool:
            raise TypeError(
                "verbose parameter can only take argument as True or False."
            )

        # checking verbose parameter
        if type(return_tuner) is not bool:
            raise TypeError(
                "return_tuner parameter can only take argument as True or False."
            )

        if not verbose:
            tuner_verbose = 0

        if type(tuner_verbose) not in (bool, int):
            raise TypeError("tuner_verbose parameter must be a bool or an int.")

        tuner_verbose = int(tuner_verbose)

        if tuner_verbose < 0:
            tuner_verbose = 0
        elif tuner_verbose > 2:
            tuner_verbose = 2

        """

        ERROR HANDLING ENDS HERE

        """

        # cross validation setup starts here
        cv = self.get_fold_generator(fold=fold)

        if not display:
            progress_args = {"max": 3 + 4}
            master_display_columns = [
                v.display_name for k, v in self._all_metrics.items()
            ]
            if self._ml_usecase == MLUsecase.TIME_SERIES:
                master_display_columns.insert(0, "cutoff")
            timestampStr = datetime.datetime.now().strftime("%H:%M:%S")
            monitor_rows = [
                ["Initiated", ". . . . . . . . . . . . . . . . . .", timestampStr],
                [
                    "Status",
                    ". . . . . . . . . . . . . . . . . .",
                    "Loading Dependencies",
                ],
                [
                    "Estimator",
                    ". . . . . . . . . . . . . . . . . .",
                    "Compiling Library",
                ],
            ]
            display = Display(
                verbose=verbose,
                html_param=self.html_param,
                progress_args=progress_args,
                master_display_columns=master_display_columns,
                monitor_rows=monitor_rows,
            )

            display.display_progress()
            display.display_monitor()
            display.display_master_display()

        # ignore warnings

        warnings.filterwarnings("ignore")

        # import logging

        np.random.seed(self.seed)

        self.logger.info("Copying training dataset")
        # Storing X_train and y_train in data_X and data_y parameter
        if self.X_train is None:
            data_X = None
        else:
            data_X = self.X_train.copy()
        data_y = self.y_train.copy()

        # # Replace Empty DataFrame with None as empty DataFrame causes issues
        # if (data_X.shape[0] == 0) or (data_X.shape[1] == 0):
        #     data_X = None

        display.move_progress()

        # setting optimize parameter
        # TODO: Changed compared to other PyCaret UseCases (Check with Antoni)
        # optimize = optimize.scorer
        compare_dimension = optimize_container.display_name
        optimize_metric_dict = {optimize_container.id: optimize_container.scorer}

        # Returns a dictionary of all metric containers (disabled for now since
        # we only need optimize metric)
        # {'mae': <pycaret.containers....783DEB0C8>, 'rmse': <pycaret.containers....783DEB148> ...}
        #  all_metric_containers = self._all_metrics

        # # Returns a dictionary of all metric scorers (disabled for now since
        # we only need optimize metric)
        # {'mae': 'neg_mean_absolute_error', 'rmse': 'neg_root_mean_squared_error' ...}
        # all_metrics_dict = {
        #     all_metric_containers[metric_id].id: all_metric_containers[metric_id].scorer
        #     for metric_id in all_metric_containers
        # }

        refit_metric = optimize_container.id  # Name of the metric: e.g. 'mae'

        # convert trained estimator into string name for grids

        self.logger.info("Checking base model")

        is_stacked_model = False

        if hasattr(estimator, "final_estimator"):
            self.logger.info("Model is stacked, using the definition of the meta-model")
            is_stacked_model = True
            estimator_id = self._get_model_id(estimator.final_estimator)
        else:
            estimator_id = self._get_model_id(estimator)
        if estimator_id is None:
            if custom_grid is None:
                raise ValueError(
                    "When passing a model not in PyCaret's model library, the custom_grid parameter must be provided."
                )
            estimator_name = self._get_model_name(estimator)
            estimator_definition = None
            self.logger.info("A custom model has been passed")
        else:
            estimator_definition = self._all_models_internal[estimator_id]  # Container
            estimator_name = estimator_definition.name
        self.logger.info(f"Base model : {estimator_name}")

        # If no special tunable class is defined inside PyCaret then just clone the estimator
        if estimator_definition is None or estimator_definition.tunable is None:
            model = clone(estimator)
        # If special tunable class is defined, then use that instead
        else:
            self.logger.info("Model has a special tunable class, using that")
            model = clone(estimator_definition.tunable(**estimator.get_params()))
        is_stacked_model = False

        # base_estimator = model

        display.update_monitor(2, estimator_name)
        display.display_monitor()

        display.move_progress()

        self.logger.info("Declaring metric variables")

        """
        MONITOR UPDATE STARTS
        """

        display.update_monitor(1, "Searching Hyperparameters")
        display.display_monitor()

        """
        MONITOR UPDATE ENDS
        """

        self.logger.info("Defining Hyperparameters")

        if search_algorithm is None:
            search_algorithm = "random"  # Defaults to Random

        ###########################
        #### Define Param Grid ----
        ###########################
        param_grid = None
        if custom_grid is not None:
            param_grid = custom_grid
            self.logger.info(f"custom_grid: {param_grid}")
        elif search_library == "pycaret":
            if search_algorithm == "grid":
                param_grid = estimator_definition.tune_grid
            elif search_algorithm == "random":
                param_grid = estimator_definition.tune_distribution

        if not param_grid:
            raise ValueError(
                "parameter grid for tuning is empty. If passing custom_grid, "
                "make sure that it is not empty. If not passing custom_grid, "
                "the passed estimator does not have a built-in tuning grid."
            )

        suffixes = []

        if is_stacked_model:
            self.logger.info(
                "Stacked model passed, will tune meta model hyperparameters"
            )
            suffixes.append("final_estimator")

        gc.collect()

        # with estimator_pipeline(self.pipeline, model) as pipeline_with_model:
        if True:

            # fit_kwargs = get_pipeline_fit_kwargs(pipeline_with_model, fit_kwargs)

            # fh_param = {"fh": cv.fh}
            # if fit_kwargs is None:
            #     fit_kwargs = fh_param
            # else:
            #     fit_kwargs.update(fh_param)
            fit_kwargs = self.update_fit_kwargs_with_fh_from_cv(
                fit_kwargs=fit_kwargs, cv=cv
            )

            # actual_estimator_label = get_pipeline_estimator_label(pipeline_with_model)
            actual_estimator_label = ""

            # suffixes.append(actual_estimator_label)

            # suffixes = "__".join(reversed(suffixes))

            # param_grid = {f"{suffixes}__{k}": v for k, v in param_grid.items()}

            if estimator_definition is not None:
                search_kwargs = {**estimator_definition.tune_args, **kwargs}
                n_jobs = (
                    self._gpu_n_jobs_param
                    if estimator_definition.is_gpu_enabled
                    else self.n_jobs_param
                )
            else:
                search_kwargs = {}
                n_jobs = self.n_jobs_param

            self.logger.info(f"Tuning with n_jobs={n_jobs}")

            if search_library == "pycaret":
                if search_algorithm == "random":
                    try:
                        param_grid = get_base_distributions(param_grid)
                    except:
                        self.logger.warning(
                            "Couldn't convert param_grid to specific library distributions. Exception:"
                        )
                        self.logger.warning(traceback.format_exc())

            if search_library == "pycaret":
                if search_algorithm == "grid":
                    self.logger.info("Initializing ForecastingGridSearchCV")

                    model_grid = ForecastingGridSearchCV(
                        forecaster=model,
                        cv=cv,
                        param_grid=param_grid,
                        scoring=optimize_metric_dict,
                        refit_metric=refit_metric,
                        n_jobs=n_jobs,
                        verbose=tuner_verbose,
                        refit=False,  # since we will refit afterwards anyway
                        **search_kwargs,
                    )
                elif search_algorithm == "random":
                    self.logger.info("Initializing ForecastingRandomizedGridSearchCV")

                    model_grid = ForecastingRandomizedSearchCV(
                        forecaster=model,
                        cv=cv,
                        param_distributions=param_grid,
                        n_iter=n_iter,
                        scoring=optimize_metric_dict,
                        refit_metric=refit_metric,
                        n_jobs=n_jobs,
                        verbose=tuner_verbose,
                        random_state=self.seed,
                        refit=False,  # since we will refit afterwards anyway
                        **search_kwargs,
                    )
                else:
                    raise NotImplementedError(
                        f"Search type '{search_algorithm}' is not supported"
                    )

            additional_scorer_kwargs = self.get_additional_scorer_kwargs()
            model_grid.fit(
                y=data_y,
                X=data_X,
                additional_scorer_kwargs=additional_scorer_kwargs,
                **fit_kwargs,
            )

            best_params = model_grid.best_params_
            self.logger.info(f"best_params: {best_params}")
            best_params = {**best_params}
            if actual_estimator_label:
                best_params = {
                    k.replace(f"{actual_estimator_label}__", ""): v
                    for k, v in best_params.items()
                }
            cv_results = None
            try:
                cv_results = model_grid.cv_results_
            except:
                self.logger.warning(
                    "Couldn't get cv_results from model_grid. Exception:"
                )
                self.logger.warning(traceback.format_exc())

        display.move_progress()

        self.logger.info("Hyperparameter search completed")

        if isinstance(model, TunableMixin):
            self.logger.info("Getting base sklearn object from tunable")
            best_params = {
                k: v
                for k, v in model.get_params().items()
                if k in model.get_base_sklearn_params().keys()
            }
            model = model.get_base_sklearn_object()

        self.logger.info(
            "SubProcess create_model() called =================================="
        )

        best_model, model_fit_time = self.create_model(
            estimator=model,
            system=False,
            display=display,
            fold=fold,
            round=round,
            fit_kwargs=fit_kwargs,
            **best_params,
        )
        model_results = self.pull()
        self.logger.info(
            "SubProcess create_model() end =================================="
        )

        if choose_better:
            best_model = self._choose_better(
                [estimator, (best_model, model_results)],
                compare_dimension,
                fold,
                fit_kwargs=fit_kwargs,
                display=display,
            )

        # end runtime
        runtime_end = time.time()
        runtime = np.array(runtime_end - runtime_start).round(2)

        # mlflow logging
        if self.logging_param:

            avgs_dict_log = {k: v for k, v in model_results.loc["Mean"].items()}

            try:
                self._mlflow_log_model(
                    model=best_model,
                    model_results=model_results,
                    score_dict=avgs_dict_log,
                    source="tune_model",
                    runtime=runtime,
                    model_fit_time=model_fit_time,
                    pipeline=self.pipeline,
                    log_plots=self.log_plots_param,
                    tune_cv_results=cv_results,
                    display=display,
                )
            except:
                self.logger.error(
                    f"_mlflow_log_model() for {best_model} raised an exception:"
                )
                self.logger.error(traceback.format_exc())

        model_results = color_df(model_results, "yellow", ["Mean"], axis=1)
        model_results = model_results.set_precision(round)
        display.display(model_results, clear=True)

        self.logger.info(f"master_model_container: {len(self.master_model_container)}")
        self.logger.info(f"display_container: {len(self.display_container)}")

        self.logger.info(str(best_model))
        self.logger.info(
            "tune_model() succesfully completed......................................"
        )

        gc.collect()
        if return_tuner:
            return (best_model, model_grid)
        return best_model

    def blend_models(
        self,
        estimator_list: list,
        method: str = "mean",
        fold: Optional[Union[int, Any]] = None,
        round: int = 4,
        choose_better: bool = False,
        optimize: str = "SMAPE",
        weights: Optional[List[float]] = None,
        fit_kwargs: Optional[dict] = None,
        verbose: bool = True,
    ):

        """
        This function trains a EnsembleForecaster for select models passed in the
        ``estimator_list`` param. The output of this function is a score grid with
        CV scores by fold. Metrics evaluated during CV can be accessed using the
        ``get_metrics`` function. Custom metrics can be added or removed using
        ``add_metric`` and ``remove_metric`` function.


        Example
        --------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)
        >>> top3 = compare_models(n_select = 3)
        >>> blender = blend_models(top3)


        estimator_list: list of sktime compatible estimators
            List of model objects


        method: str, default = 'mean'
            Method to average the individual predictions to form a final prediction.
            Available Methods:

            * 'mean' - Mean of individual predictions
            * 'median' - Median of individual predictions
            * 'voting' - Vote individual predictions based on the provided weights.


        fold: int or scikit-learn compatible CV generator, default = None
            Controls cross-validation. If None, the CV generator in the ``fold_strategy``
            parameter of the ``setup`` function is used. When an integer is passed,
            it is interpreted as the 'n_splits' parameter of the CV generator in the
            ``setup`` function.


        round: int, default = 4
            Number of decimal places the metrics in the score grid will be rounded to.


        choose_better: bool, default = False
            When set to True, the returned object is always better performing. The
            metric used for comparison is defined by the ``optimize`` parameter.


        optimize: str, default = 'SMAPE'
            Metric to compare for model selection when ``choose_better`` is True.


        weights: list, default = None
            Sequence of weights (float or int) to weight the occurrences of predicted class
            labels (hard voting) or class probabilities before averaging (soft voting). Uses
            uniform weights when None.


        fit_kwargs: dict, default = {} (empty dict)
            Dictionary of arguments passed to the fit method of the model.


        verbose: bool, default = True
            Score grid is not printed when verbose is set to False.


        Returns:
            Trained Model


        """

        return super().blend_models(
            estimator_list=estimator_list,
            fold=fold,
            round=round,
            choose_better=choose_better,
            optimize=optimize,
            method=method,
            weights=weights,
            fit_kwargs=fit_kwargs,
            verbose=verbose,
        )

    def plot_model(
        self,
        estimator: Optional[Any] = None,
        plot: Optional[str] = None,
        return_fig: bool = False,
        return_data: bool = False,
        verbose: bool = False,
        display_format: Optional[str] = None,
        data_kwargs: Optional[Dict] = None,
        fig_kwargs: Optional[Dict] = None,
        system: bool = True,
        save: Union[str, bool] = False,
    ) -> Optional[Tuple[str, Any]]:

        """
        This function analyzes the performance of a trained model on holdout set.
        When used without any estimator, this function generates plots on the
        original data set. When used with an estimator, it will generate plots on
        the model residuals.


        Example
        --------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)
        >>> plot_model(plot="diff", data_kwargs={"order_list": [1, 2], "acf": True, "pacf": True})
        >>> plot_model(plot="diff", data_kwargs={"lags_list": [[1], [1, 12]], "acf": True, "pacf": True})
        >>> arima = create_model('arima')
        >>> plot_model(plot = 'ts')
        >>> plot_model(plot = 'decomp', data_kwargs = {'type' : 'multiplicative'})
        >>> plot_model(plot = 'decomp', data_kwargs = {'seasonal_period': 24})
        >>> plot_model(estimator = arima, plot = 'forecast', data_kwargs = {'fh' : 24})


        estimator: sktime compatible object, default = None
            Trained model object


        plot: str, default = None
            Default is 'ts' when estimator is None, When estimator is not None,
            default is changed to 'forecast'. List of available plots (ID - Name):

            * 'ts' - Time Series Plot
            * 'train_test_split' - Train Test Split
            * 'cv' - Cross Validation
            * 'acf' - Auto Correlation (ACF)
            * 'pacf' - Partial Auto Correlation (PACF)
            * 'decomp' - Classical Decomposition
            * 'decomp_stl' - STL Decomposition
            * 'diagnostics' - Diagnostics Plot
            * 'diff' - Difference Plot
            * 'periodogram' - Frequency Components (Periodogram)
            * 'fft' - Frequency Components (FFT)
            * 'ccf' - Cross Correlation (CCF)
            * 'forecast' - "Out-of-Sample" Forecast Plot
            * 'insample' - "In-Sample" Forecast Plot
            * 'residuals' - Residuals Plot


        return_fig: : bool, default = False
            When set to True, it returns the figure used for plotting.


        return_data: bool, default = False
            When set to True, it returns the data for plotting.
            If both return_fig and return_data is set to True, order of return
            is figure then data.


        verbose: bool, default = True
            Unused for now


        display_format: str, default = None
            To display plots in Streamlit (https://www.streamlit.io/), set this to 'streamlit'.
            Currently, not all plots are supported.


        data_kwargs: dict, default = None
            Dictionary of arguments passed to the data for plotting.


        fig_kwargs: dict, default = {} (empty dict)
            The setting to be used for the plot. Overrides any global setting
            passed during setup. Pass these as key-value pairs. For available
            keys, refer to the `setup` documentation.


        save: string or bool, default = False
            When set to True, Plot is saved as a 'png' file in current working directory.
            When a path destination is given, Plot is saved as a 'png' file the given path to the directory of choice.


        Returns:
            Optional[Tuple[str, Any]]

        """
        # checking display_format parameter
        self.plot_model_check_display_format_(display_format=display_format)

        if plot == "decomp_classical":
            msg = (
                "DeprecationWarning: `decomp_classical` plot type will be disabled in "
                "a future release. Please use `decomp` instead."
            )
            warnings.warn(msg, DeprecationWarning)
            print(msg)
            #### Reset to "decomp"
            plot = "decomp"

        # Import required libraries ----
        if display_format == "streamlit":
            try:
                import streamlit as st
            except ImportError:
                raise ImportError(
                    "It appears that streamlit is not installed. Do: pip install streamlit"
                )

        # Add sp value (used in decomp plots)
        data_kwargs = data_kwargs or {}
        data_kwargs.setdefault("seasonal_period", self.primary_sp_to_use)

        fig_kwargs = fig_kwargs or {}

        return_pred_int = False
        return_obj = []

        # Type checks
        if estimator is not None and isinstance(estimator, str):
            raise ValueError(
                "Estimator must be a trained object. "
                f"You have passed a string: '{estimator}'"
            )

        # Default plot when no model is specified is the time series plot
        # Default plot when model is specified is the forecast plot
        if plot is None and estimator is None:
            plot = "ts"
        elif plot is None and estimator is not None:
            plot = "forecast"

        data, train, test, X, predictions, cv, model_name = (
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )

        include = data_kwargs.get("include", None)
        exclude = data_kwargs.get("exclude", None)

        if plot == "ts":
            data = self._get_y_data(split="all")
            X = self._get_X_data(split="all", include=include, exclude=exclude)
        elif plot == "train_test_split":
            train = self._get_y_data(split="train")
            test = self._get_y_data(split="test")
        elif plot == "cv":
            data = self._get_y_data(split="train")
            cv = self.get_fold_generator()
        elif plot == "ccf":
            data = self._get_y_data(split="all")
            X = self._get_X_data(split="all", include=include, exclude=exclude)
        elif estimator is None:
            # Estimator is not provided
            require_full_data = [
                "acf",
                "pacf",
                "diagnostics",
                "decomp",
                "decomp_stl",
                "diff",
                "periodogram",
                "fft",
            ]
            if plot in require_full_data:
                data = self._get_y_data(split="all")
            else:
                plots_formatted_data = [
                    f"'{plot}'" for plot in self._available_plots_data_keys
                ]
                raise ValueError(
                    f"Plot type '{plot}' is not supported when estimator is not "
                    f"provided. Available plots are: {', '.join(plots_formatted_data)}"
                )
        else:
            # Estimator is Provided

            if hasattr(self, "_get_model_name") and hasattr(
                self, "_all_models_internal"
            ):
                model_name = self._get_model_name(estimator)
            else:
                # If the model is saved and loaded afterwards,
                # it will not have self._get_model_name
                model_name = estimator.__class__.__name__

            require_insample_predictions = ["insample"]
            require_residuals = [
                "residuals",
                "diagnostics",
                "acf",
                "pacf",
                "decomp",
                "decomp_stl",
                "diff",
                "periodogram",
                "fft",
            ]
            if plot == "forecast":
                data = self._get_y_data(split="all")

                fh = data_kwargs.get("fh", None)
                alpha = data_kwargs.get("alpha", 0.05)
                X = data_kwargs.get("X", None)
                return_pred_int = estimator.get_tag("capability:pred_int")

                predictions = self.predict_model(
                    estimator,
                    fh=fh,
                    X=X,
                    alpha=alpha,
                    return_pred_int=return_pred_int,
                    verbose=False,
                )
            elif plot in require_insample_predictions:
                # Try to get insample forecasts if possible
                insample_predictions = self.get_insample_predictions(
                    estimator=estimator
                )
                if insample_predictions is None:
                    return
                predictions = insample_predictions
                data = self._get_y_data(split="all")
                # Do not plot prediction interval for insample predictions
                return_pred_int = False

            elif plot in require_residuals:
                resid = self.get_residuals(estimator=estimator)
                if resid is None:
                    return
                data = resid
            else:
                plots_formatted_model = [
                    f"'{plot}'" for plot in self._available_plots_estimator_keys
                ]
                raise ValueError(
                    f"Plot type '{plot}' is not supported when estimator is provided. "
                    f"Available plots are: {', '.join(plots_formatted_model)}"
                )

        fig, plot_data = _get_plot(
            plot=plot,
            fig_defaults=self.fig_kwargs,
            data=data,
            train=train,
            test=test,
            X=X,
            predictions=predictions,
            cv=cv,
            model_name=model_name,
            return_pred_int=return_pred_int,
            data_kwargs=data_kwargs,
            fig_kwargs=fig_kwargs,
        )

        # Sometimes the plot is not successful, such as decomp with RangeIndex.
        # In such cases, plotting should be bypassed.
        if fig is not None:
            plot_name = self._available_plots[plot]
            plot_filename = f"{plot_name}.html"

            # Per https://github.com/pycaret/pycaret/issues/1699#issuecomment-962460539
            if save:
                if not isinstance(save, bool):
                    plot_filename = os.path.join(save, plot_filename)

                self.logger.info(f"Saving '{plot_filename}'")
                fig.write_html(plot_filename)

                ### Add file name to return object ----
                return_obj.append(plot_filename)

            elif system:
                if display_format == "streamlit":
                    st.write(fig)
                else:
                    try:
                        big_data_threshold = _resolve_dict_keys(
                            dict_=fig_kwargs,
                            key="big_data_threshold",
                            defaults=self.fig_kwargs,
                        )
                        renderer = _resolve_dict_keys(
                            dict_=fig_kwargs, key="renderer", defaults=self.fig_kwargs
                        )
                        renderer = _resolve_renderer(
                            renderer=renderer,
                            threshold=big_data_threshold,
                            data=data,
                            train=train,
                            test=test,
                            X=X,
                        )
                        fig.show(renderer=renderer)
                        self.logger.info("Visual Rendered Successfully")
                    except ValueError as exception:
                        self.logger.info(exception)
                        self.logger.info("Visual Rendered Unsuccessfully")
                        print(exception)
                        print(
                            "When data exceeds a certain threshold (determined by "
                            "`big_data_threshold`), the renderer is switched to a "
                            "static one to prevent notebooks from being slowed down.\n"
                            "This renderer may need to be installed manually by users.\n"
                            "Alternately:\n"
                            "Option 1: "
                            "Users can increase `big_data_threshold` in either `setup` "
                            "(globally) or `plot_model` (plot specific). Examples.\n"
                            "\t>>> setup(..., fig_kwargs={'big_data_threshold': 1000})\n"
                            "\t>>> plot_model(..., fig_kwargs={'big_data_threshold': 1000})\n"
                            "Option 2: "
                            "Users can specify any plotly renderer directly in either `setup` "
                            "(globally) or `plot_model` (plot specific). Examples.\n"
                            "\t>>> setup(..., fig_kwargs={'renderer': 'notebook'})\n"
                            "\t>>> plot_model(..., fig_kwargs={'renderer': 'colab'})\n"
                            "Refer to the docstring in `setup` for more details."
                        )

        ### Add figure and data to return object if required ----
        if return_fig:
            return_obj.append(fig)
        if return_data:
            return_obj.append(plot_data)

        #### Return None if empty, return as list if more than one object,
        # else return object directly ----
        if len(return_obj) == 0:
            return_obj = None
        elif len(return_obj) == 1:
            return_obj = return_obj[0]
        return return_obj

    def predict_model(
        self,
        estimator,
        fh=None,
        X=None,
        return_pred_int=False,
        alpha=0.05,
        round: int = 4,
        verbose: bool = True,
    ) -> pd.DataFrame:

        """
        This function forecast using a trained model. When ``fh`` is None,
        it forecasts using the same forecast horizon used during the
        training.


        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)
        >>> arima = create_model('arima')
        >>> pred_holdout = predict_model(arima)
        >>> pred_unseen = predict_model(finalize_model(arima), fh = 24)


        estimator: sktime compatible object
            Trained model object


        fh: int, default = None
            Number of points from the last date of training to forecast.
            When fh is None, it forecasts using the same forecast horizon
            used during the training.


        X: pd.DataFrame, default = None
            Exogenous Variables to be used for prediction.
            Before finalizing the estimator, X need not be passed even when the
            estimator is built using exogenous variables (since this is taken
            care of internally by using the exogenous variables from test split).
            When estimator has been finalized and estimator used exogenous
            variables, then X must be passed.


        return_pred_int: bool, default = False
            When set to True, it returns lower bound and upper bound
            prediction interval, in addition to the point prediction.


        alpha: float, default = 0.05
            alpha for prediction interval. CI = 1 - alpha.


        round: int, default = 4
            Number of decimal places to round predictions to.


        verbose: bool, default = True
            When set to False, holdout score grid is not printed.


        Returns:
            pandas.DataFrame


        """
        estimator.check_is_fitted()

        # Deep Cloning to prevent overwriting the fh when user specifies their own fh
        estimator_ = deep_clone(estimator)
        pipeline_with_model = _add_model_to_pipeline(
            pipeline=self.pipeline, model=estimator_
        )

        loaded_in_same_env = True
        # Check if loaded in a different environment
        if not hasattr(self, "X_test") or fh is not None:
            # If the model is saved and loaded afterwards,
            # it will not have self.X_test

            # Also do not display metrics if user provides own fh
            # (even if it is same as test set horizon) per
            # https://github.com/pycaret/pycaret/issues/1702
            loaded_in_same_env = False
            verbose = False

        if fh is not None:
            # Do not display metrics if user provides own fh
            # (even if it is same as test set horizon) per
            # https://github.com/pycaret/pycaret/issues/1702
            verbose = False

        if fh is None:
            # TODO: Check if we can directly take from the estimator and remove the if-else
            if not hasattr(self, "fh"):
                # If the model is saved and loaded afterwards,
                # it will not have self.fh
                fh = estimator_.fh
            else:
                fh = self.fh
        else:
            # Get the fh in the right format for sktime
            fh = self._check_fh(fh)

        if hasattr(self, "X_test"):
            # If loaded in the same environment as experiment & model has not been
            # finalized, then set X = X_test.
            estimator_y, _ = self._get_cleaned_estimator_y_X(estimator=estimator)
            if estimator_y.index[-1] == self.y_train.index[-1] and X is None:
                X = self.X_test  # Predict Test Set
        # else: # Loaded in different environment
        # NOTE: If the model was built using exogenous variables, then user
        # must make sure that this is provided, else the predictions will fail.

        #### Convert to None if empty dataframe
        # Some predict methods in sktime expect None (not an empty dataframe as
        # returned by pycaret). Hence converting to None.
        X = _coerce_empty_dataframe_to_none(data=X)

        result = get_predictions_with_intervals(
            forecaster=pipeline_with_model,
            X=X,
            fh=fh,
            alpha=alpha,
            merge=True,
            round=round,
        )
        if not return_pred_int:
            result = result["y_pred"]

        #################
        #### Metrics ####
        #################
        # Only display if loaded in same environment

        # This is not technically y_test_pred in all cases.
        # If the model has not been finalized, y_test_pred will match the indices from y_test
        # If the model has been finalized, y_test_pred will not match the indices from y_test
        # Also, the user can use a different fh length in predict in which case the length
        # of y_test_pred will not match y_test.

        if loaded_in_same_env:
            y_test = self.y_test
            y_train, _ = self._get_y_X_used_for_training(estimator)

            y_test_pred, lower, upper = get_predictions_with_intervals(
                forecaster=pipeline_with_model, X=X, fh=fh, alpha=alpha
            )

            if len(y_test_pred) != len(y_test):
                msg = (
                    "predict_model >> Forecast Horizon does not match the horizon length "
                    "used during training. Metrics will not be displayed."
                )
                self.logger.warning(msg)
                verbose = False

            # concatenates by index
            y_test_and_pred = pd.concat([y_test_pred, y_test], axis=1)
            # Removes any indices that do not match
            y_test_and_pred.dropna(inplace=True)
            y_test_pred_common = y_test_and_pred[y_test_and_pred.columns[0]]
            y_test_common = y_test_and_pred[y_test_and_pred.columns[1]]

            if len(y_test_and_pred) == 0:
                self.logger.warning(
                    "predict_model >> No indices matched between test set and prediction. "
                    "You are most likely calling predict_model after finalizing model. "
                    "Metrics will not be displayed."
                )
                metrics = self._calculate_metrics(y_test=[], pred=[], pred_prob=None)  # type: ignore
                metrics = {metric_name: np.nan for metric_name, _ in metrics.items()}
                verbose = False
            else:
                # Pass additional keyword arguments (like y_train, lower, upper) to
                # method since they need to be passed to certain metrics like MASE,
                # INPI, etc. This method will internally orchestrate the passing of
                # the right arguments to the scorers.
                initial_kwargs = self.get_additional_scorer_kwargs()
                additional_scorer_kwargs = update_additional_scorer_kwargs(
                    initial_kwargs=initial_kwargs,
                    y_train=y_train,
                    lower=lower,
                    upper=upper,
                )
                metrics = self._calculate_metrics(
                    y_test=y_test_common,
                    pred=y_test_pred_common,
                    pred_prob=None,
                    **additional_scorer_kwargs,
                )  # type: ignore

            # Display Test Score
            # model name
            display = None
            try:
                np.random.seed(self.seed)
                if not display:
                    display = Display(verbose=verbose, html_param=self.html_param)
            except:
                display = Display(verbose=False, html_param=False)

            full_name = self._get_model_name(estimator_)
            df_score = pd.DataFrame(metrics, index=[0])
            df_score.insert(0, "Model", full_name)
            df_score = df_score.round(round)
            display.display(df_score.style.set_precision(round), clear=False)

            # store predictions on hold-out in display_container
            if df_score is not None:
                self.display_container.append(df_score)

        gc.collect()

        return result

    def finalize_model(
        self,
        estimator,
        fit_kwargs: Optional[dict] = None,
        model_only: bool = True,
        experiment_custom_tags: Optional[Dict[str, Any]] = None,
    ) -> Any:

        """
        This function trains a given estimator on the entire dataset including the
        holdout set.


        Example
        --------
        >>> from pycaret.datasets import get_data
        >>> data = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = data, fh = 12)
        >>> arima = create_model('arima')
        >>> final_arima = finalize_model(arima)


        estimator: sktime compatible object
            Trained model object


        fit_kwargs: dict, default = None
            Dictionary of arguments passed to the fit method of the model.


        model_only: bool, default = True
            Parameter not in use for now. Behavior may change in future.


        Returns:
            Trained Model


        """

        return super().finalize_model(
            estimator=estimator,
            fit_kwargs=fit_kwargs,
            model_only=model_only,
            experiment_custom_tags=experiment_custom_tags,
        )

    def deploy_model(
        self,
        model,
        model_name: str,
        authentication: dict,
        platform: str = "aws",
    ):

        """
        This function deploys the transformation pipeline and trained model on cloud.


        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> data = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = data, fh = 12)
        >>> arima = create_model('arima')
        >>> deploy_model(
                model = arima, model_name = 'arima-for-deployment',
                platform = 'aws', authentication = {'bucket' : 'S3-bucket-name'}
            )


        Amazon Web Service (AWS) users:
            To deploy a model on AWS S3 ('aws'), environment variables must be set in your
            local environment. To configure AWS environment variables, type ``aws configure``
            in the command line. Following information from the IAM portal of amazon console
            account is required:

            - AWS Access Key ID
            - AWS Secret Key Access
            - Default Region Name (can be seen under Global settings on your AWS console)

            More info: https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html


        Google Cloud Platform (GCP) users:
            To deploy a model on Google Cloud Platform ('gcp'), project must be created
            using command line or GCP console. Once project is created, you must create
            a service account and download the service account key as a JSON file to set
            environment variables in your local environment.

            More info: https://cloud.google.com/docs/authentication/production


        Microsoft Azure (Azure) users:
            To deploy a model on Microsoft Azure ('azure'), environment variables for connection
            string must be set in your local environment. Go to settings of storage account on
            Azure portal to access the connection string required.

            More info: https://docs.microsoft.com/en-us/azure/storage/blobs/storage-quickstart-blobs-python?toc=%2Fpython%2Fazure%2FTOC.json


        model: scikit-learn compatible object
            Trained model object


        model_name: str
            Name of model.


        authentication: dict
            Dictionary of applicable authentication tokens.

            When platform = 'aws':
            {'bucket' : 'S3-bucket-name', 'path': (optional) folder name under the bucket}

            When platform = 'gcp':
            {'project': 'gcp-project-name', 'bucket' : 'gcp-bucket-name'}

            When platform = 'azure':
            {'container': 'azure-container-name'}


        platform: str, default = 'aws'
            Name of the platform. Currently supported platforms: 'aws', 'gcp' and 'azure'.


        Returns:
            None

        """

        return super().deploy_model(
            model=model,
            model_name=model_name,
            authentication=authentication,
            platform=platform,
        )

    def save_model(
        self, model, model_name: str, model_only: bool = False, verbose: bool = True
    ):

        """
        This function saves the transformation pipeline and trained model object
        into the current working directory as a pickle file for later use.

        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> data = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = data, fh = 12)
        >>> arima = create_model('arima')
        >>> save_model(arima, 'saved_arima_model')


        model: sktime compatible object
            Trained model object


        model_name: str
            Name of the model.


        model_only: bool, default = False
            When set to True, only trained model object is saved instead of the
            entire pipeline.


        verbose: bool, default = True
            Success message is not printed when verbose is set to False.


        Returns:
            Tuple of the model object and the filename.

        """

        return super().save_model(
            model=model, model_name=model_name, model_only=model_only, verbose=verbose
        )

    def load_model(
        self,
        model_name,
        platform: Optional[str] = None,
        authentication: Optional[Dict[str, str]] = None,
        verbose: bool = True,
    ):

        """
        This function loads a previously saved pipeline/model.

        Example
        -------
        >>> from pycaret.time_series import load_model
        >>> saved_arima = load_model('saved_arima_model')


        model_name: str
            Name of the model.


        platform: str, default = None
            Name of the cloud platform. Currently supported platforms:
            'aws', 'gcp' and 'azure'.


        authentication: dict, default = None
            dictionary of applicable authentication tokens.

            when platform = 'aws':
            {'bucket' : 'S3-bucket-name'}

            when platform = 'gcp':
            {'project': 'gcp-project-name', 'bucket' : 'gcp-bucket-name'}

            when platform = 'azure':
            {'container': 'azure-container-name'}


        verbose: bool, default = True
            Success message is not printed when verbose is set to False.


        Returns:
            Trained Model

        """

        model = super().load_model(
            model_name=model_name,
            platform=platform,
            authentication=authentication,
            verbose=verbose,
        )

        #### In time series, the fitted pipeline is needed to make predictions,
        # hence must be set.
        if isinstance(model, PyCaretForecastingPipeline):
            # The saved model was saved with the pipeline.
            # Set the pipeline as it is needed for predictions, etc.
            self.pipeline = deepcopy(model)
        else:
            # The saved model was saved without the pipeline. Create a dummy one
            # and fit it.
            self.pipeline = self._create_pipeline(dummy_model=model)
            self.fh = model.fh
            self.pipeline.fit(y=model._y, X=model._X, fh=self.fh)

        self.pipeline.check_is_fitted()
        model = self._get_final_model_from_pipeline(
            pipeline=self.pipeline, check_is_fitted=True
        )
        return model

    def _create_pipeline(
        self,
        dummy_model: BaseForecaster,
        target_steps: Optional[List] = None,
        exogenous_steps: Optional[List] = None,
    ) -> PyCaretForecastingPipeline:

        target_steps = target_steps or []
        exogenous_steps = exogenous_steps or []

        # Set the pipeline from model
        # Add dummy forecaster for now ----
        target_steps.extend([("dummy_model", dummy_model)])
        forecaster = TransformedTargetForecaster(target_steps)

        # Create Forecasting Pipeline ----
        exogenous_steps.extend([("forecaster", forecaster)])
        pipeline = PyCaretForecastingPipeline(exogenous_steps)

        return pipeline

    def models(
        self,
        type: Optional[str] = None,
        internal: bool = False,
        raise_errors: bool = True,
    ) -> pd.DataFrame:

        """
        Returns table of models available in the model library.

        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> data = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = data, fh = 12)
        >>> models()


        type: str, default = None
            - baseline : filters and only return baseline models
            - classical : filters and only return classical models
            - linear : filters and only return linear models
            - tree : filters and only return tree based models
            - neighbors : filters and only return neighbors models


        internal: bool, default = False
            When True, will return extra columns and rows used internally.


        raise_errors: bool, default = True
            When False, will suppress all exceptions, ignoring models
            that couldn't be created.


        Returns:
            pandas.DataFrame

        """
        self.logger.info(f"gpu_param set to {self.gpu_param}")

        model_types = list(TSModelTypes)

        if type:
            try:
                type = TSModelTypes(type)
            except ValueError:
                raise ValueError(
                    f"type parameter only accepts: {', '.join([x.value for x in TSModelTypes.__members__.values()])}."
                )

            model_types = [type]

        _, model_containers = self._get_models(raise_errors)

        model_containers = {
            k: v for k, v in model_containers.items() if v.model_type in model_types
        }

        rows = [
            v.get_dict(internal)
            for k, v in model_containers.items()
            if (internal or not v.is_special)
        ]

        df = pd.DataFrame(rows)
        df.set_index("ID", inplace=True, drop=True)

        return df

    def get_metrics(
        self,
        reset: bool = False,
        include_custom: bool = True,
        raise_errors: bool = True,
    ) -> pd.DataFrame:

        """
        Returns table of available metrics used for CV.


        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)
        >>> all_metrics = get_metrics()


        reset: bool, default = False
            When True, will reset all changes made using the ``add_metric``
            and ``remove_metric`` function.


        include_custom: bool, default = True
            Whether to include user added (custom) metrics or not.


        raise_errors: bool, default = True
            If False, will suppress all exceptions, ignoring models that
            couldn't be created.


        Returns:
            pandas.DataFrame

        """

        return super().get_metrics(
            reset=reset, include_custom=include_custom, raise_errors=raise_errors
        )

    def add_metric(
        self,
        id: str,
        name: str,
        score_func: type,
        greater_is_better: bool = True,
        **kwargs,
    ) -> pd.Series:

        """
        Adds a custom metric to be used for CV.


        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)
        >>> from sklearn.metrics import explained_variance_score
        >>> add_metric('evs', 'EVS', explained_variance_score)


        id: str
            Unique id for the metric.


        name: str
            Display name of the metric.


        score_func: type
            Score function (or loss function) with signature ``score_func(y, y_pred, **kwargs)``.


        greater_is_better: bool, default = True
            Whether ``score_func`` is higher the better or not.


        **kwargs:
            Arguments to be passed to score function.


        Returns:
            pandas.Series

        """

        return super().add_metric(
            id=id,
            name=name,
            score_func=score_func,
            target="pred",
            greater_is_better=greater_is_better,
            **kwargs,
        )

    def remove_metric(self, name_or_id: str):

        """
        Removes a metric from CV.


        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> data = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = data, fh = 12)
        >>> remove_metric('MAPE')


        name_or_id: str
            Display name or ID of the metric.


        Returns:
            None

        """
        return super().remove_metric(name_or_id=name_or_id)

    def get_logs(
        self, experiment_name: Optional[str] = None, save: bool = False
    ) -> pd.DataFrame:

        """
        Returns a table of experiment logs. Only works when ``log_experiment``
        is True when initializing the ``setup`` function.


        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> data = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = data, fh = 12)
        >>> best = compare_models()
        >>> exp_logs = get_logs()


        experiment_name: str, default = None
            When None current active run is used.


        save: bool, default = False
            When set to True, csv file is saved in current working directory.


        Returns:
            pandas.DataFrame

        """

        return super().get_logs(experiment_name=experiment_name, save=save)

    def get_fold_generator(
        self,
        fold: Optional[Union[int, Any]] = None,
        fold_strategy: Optional[str] = None,
    ) -> Union[ExpandingWindowSplitter, SlidingWindowSplitter]:
        """Returns the cv object based on number of folds and fold_strategy

        Parameters
        ----------
        fold : Optional[Union[int, Any]]
            The number of folds (int), by default None which returns the fold generator
            (cv object) defined during setup. Could also be a sktime cross-validation object.
            If it is a sktime cross-validation object, it is simply returned back
        fold_strategy : Optional[str], optional
            The fold strategy - 'expanding' or 'sliding', by default None which
            takes the strategy set during `setup`

        Returns
        -------
        Union[ExpandingWindowSplitter, SlidingWindowSplitter]
            The cross-validation object

        Raises
        ------
        ValueError
            If not enough data points to support the number of folds requested
        """
        # cross validation setup starts here
        if fold is None:
            # Get cv object defined during setup
            if self.fold_generator is None:
                raise ValueError(
                    "Trying to retrieve Fold Generator but this has not been defined yet."
                )
            fold_generator = self.fold_generator
        elif not isinstance(fold, int):
            return fold  # assumes fold is an sktime compatible cross-validation object
        else:
            # Get new cv object based on the fold parameter
            y_size = len(self.y_train)

            # Changes to Max to take into account gaps in fh
            # e.g. fh=np.arange(25,73)
            # - see https://github.com/pycaret/pycaret/issues/1865
            fh_max_length = max(self.fh)

            # Step length will always end up being <= fh_max_length
            # since it is based on fh
            step_length = len(self.fh)

            initial_window = y_size - ((fold - 1) * step_length + 1 * fh_max_length)

            if initial_window < 1:
                raise ValueError(
                    "Not Enough Data Points, set a lower number of folds or fh"
                )

            # If None, get the strategy defined in the setup (e.g. `expanding`, 'sliding`, etc.)
            if fold_strategy is None:
                fold_strategy = self.fold_strategy

            if fold_strategy == "expanding" or fold_strategy == "rolling":
                fold_generator = ExpandingWindowSplitter(
                    initial_window=initial_window,
                    step_length=step_length,
                    fh=self.fh,
                    start_with_window=True,
                )

            if fold_strategy == "sliding":
                fold_generator = SlidingWindowSplitter(
                    step_length=step_length,
                    window_length=initial_window,
                    fh=self.fh,
                    start_with_window=True,
                )
        return fold_generator

    def check_stats(
        self,
        estimator: Optional[Any] = None,
        test: str = "all",
        alpha: float = 0.05,
        split: str = "all",
        data_kwargs: Optional[Dict] = None,
    ) -> pd.DataFrame:
        """This function is used to get summary statistics and run statistical
        tests on the original data or model residuals.

        Example
        --------
        >>> from pycaret.datasets import get_data
        >>> airline = get_data('airline')
        >>> from pycaret.time_series import *
        >>> exp_name = setup(data = airline,  fh = 12)
        >>> check_stats(test="summary")
        >>> check_stats(test="adf")
        >>> arima = create_model('arima')
        >>> check_stats(arima, test = 'white_noise')


        Parameters
        ----------
        estimator : sktime compatible object, optional
            Trained model object, by default None


        test : str, optional
            Name of the test to be performed, by default "all"

            Options are:

            * 'summary' - Summary Statistics
            * 'white_noise' - Ljung-Box Test for white noise
            * 'adf' - ADF test for difference stationarity
            * 'kpss' - KPSS test for trend stationarity
            * 'stationarity' - ADF and KPSS test
            * 'normality' - Shapiro Test for Normality
            * 'all' - All of the above tests


        alpha : float, optional
            Significance Level, by default 0.05


        split : str, optional
            The split of the original data to run the test on. Only applicable
            when test is run on the original data (not residuals), by default "all"

            Options are:

            * 'all' - Complete Dataset
            * 'train' - The Training Split of the dataset
            * 'test' - The Test Split of the dataset


        data_kwargs : Optional[Dict], optional
            Users can specify `lags list` or `order_list` to run the test for the
            data as well as for its lagged versions, by default None

            >>> check_stats(test="white_noise", data_kwargs={"order_list": [1, 2]})
            >>> check_stats(test="white_noise", data_kwargs={"lags_list": [1, [1, 12]]})


        Returns:
        --------
        pd.DataFrame
            Dataframe with the test results
        """

        #### Step 1: Get the data to be tested ----
        if estimator is None:
            data = self._get_y_data(split=split)
            data_name = "Actual"
        else:
            data = self.get_residuals(estimator=estimator)
            if data is None:
                return
            data_name = "Residual"

        #### Step 2: Test ----
        results = run_test(
            data=data,
            test=test,
            data_name=data_name,
            alpha=alpha,
            data_kwargs=data_kwargs,
        )
        results.reset_index(inplace=True, drop=True)
        return results

    def _get_y_data(self, split="all") -> pd.Series:
        """Returns the y data for the requested split

        Parameters
        ----------
        split : str, optional
            The plot for which the data must be returned. Options are: "all",
            "train" or "test", by default "all".

        Returns
        -------
        pd.Series
            The y values for the requested split

        Raises
        ------
        ValueError
            When `split` is not one of the allowed types
        """
        if split == "all":
            data = self.y
        elif split == "train":
            data = self.y_train
        elif split == "test":
            data = self.y_test
        else:
            raise ValueError(f"split value: '{split}' is not supported.")
        return data

    def _get_X_data(
        self,
        split="all",
        include: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """Returns the X data for the requested split

        Parameters
        ----------
        split : str, optional
            The plot for which the data must be returned. Options are: "all",
            "train" or "test", by default "all".
        include : Optional[List[str]], optional
            The columns to include in the returned data, by default None which
            returns all the columns
        exclude : Optional[List[str]], optional
            The columns to exclude from the returned data, by default None which
            does not exclude any columns

        Returns
        -------
        pd.DataFrame
            The X values for the requested split

        Raises
        ------
        ValueError
            When `split` is not one of the allowed types
        """
        if split == "all":
            data = self.X
        elif split == "train":
            data = self.X_train
        elif split == "test":
            data = self.X_test
        else:
            raise ValueError(f"split value: '{split}' is not supported.")

        # TODO: Move this functionality (of including/excluding cols) to some utility module.
        if include:
            data = data[include]
        if exclude:
            data = data.loc[:, ~data.columns.isin(exclude)]

        return data

    def _get_cleaned_estimator_y_X(
        self, estimator: BaseForecaster
    ) -> Tuple[pd.Series, pd.DataFrame]:
        """Some models like Prophet train on DatetimeIndex, but pycaret stores
        all indices as PeriodIndex. This method will convert the y and X values of
        the estimator from DatetimeIndex to PeriodIndex and return them. If the
        index is not of type DatetimeIndex, then it returns the y and X values as is.

        Note that this estimator data is different from the data used to train the
        pipeline. Because of transformatons in the pipeline, the estimator (y, X)
        values may be different from the (self.y_train, self.X_train) or
        (self.y, self.X) values passed to the pipeline.

        Parameters
        ----------
        estimator : BaseForecaster
            Estimator whose y and X values have to be cleaned and returned

        Returns
        -------
        Tuple[pd.Series, pd.DataFrame]
            Cleaned y and X values respectively
        """

        orig_freq = None
        if isinstance(estimator._y.index, pd.DatetimeIndex):
            orig_freq = self.y_train.index.freq
            clean_y = coerce_datetime_to_period_index(data=estimator._y, freq=orig_freq)
            clean_X = coerce_datetime_to_period_index(data=estimator._X, freq=orig_freq)
        else:
            clean_y = estimator._y.copy()
            if isinstance(estimator._X, pd.DataFrame):
                clean_X = estimator._X.copy()
            elif estimator._X is None:
                clean_X = None
            else:
                raise ValueError(
                    "Estimator's X is not of allowed type (Pandas DataFrame or None). "
                    f"Got {type(estimator._X)}"
                )

        return clean_y, clean_X

    def _get_y_X_used_for_training(
        self,
        estimator: BaseForecaster,
    ) -> Tuple[pd.Series, pd.DataFrame]:
        """Returns the y and X values passed to the pipeline for training.
        These values are the values before transformation and can be passed to
        the complete pipeline again if needed for steps in the workflow.

        Parameters
        ----------
        estimator : BaseForecaster
            sktime compatible model (without the pipeline). i.e. last step of
            the pipeline TransformedTargetForecaster

        Returns
        -------
        Tuple[pd.DataFrame, pd.DataFrame]
            y and X values respectively used for training

        Raises
        ------
        ValueError
            Indices used to train estimator does not match either y_train or y indices.
        """

        estimator_y, _ = self._get_cleaned_estimator_y_X(estimator=estimator)
        if len(estimator_y) == len(self.y_train) and np.all(
            estimator_y.index == self.y_train.index
        ):
            # Model has not been finalized ----
            y = self.y_train
            X = self.X_train
        elif len(estimator_y) == len(self.y) and np.all(
            estimator_y.index == self.y.index
        ):
            # Model has been finalized ----
            y = self.y
            X = self.X
        else:
            # Should not happen
            raise ValueError(
                "y indices in estimator (used for training) do not match with train "
                "or full dataset. This should not happen"
            )
        return y, X

    def get_residuals(self, estimator: BaseForecaster) -> Optional[pd.Series]:
        """_summary_

        Parameters
        ----------
        estimator : BaseForecaster
            sktime compatible model (without the pipeline). i.e. last step of
            the pipeline TransformedTargetForecaster

        Returns
        -------
        Optional[pd.Series]
            Insample residuals. `None` if estimator does not support insample predictions

        References
        ----------
        https://github.com/alan-turing-institute/sktime/issues/1105#issuecomment-932216820
        """

        resid = None

        y, _ = self._get_y_X_used_for_training(estimator)

        insample_predictions = self.get_insample_predictions(estimator)
        if insample_predictions is not None:
            resid = y - insample_predictions
            resid = self._check_and_clean_resid(resid=resid)
        else:
            print(
                "In sample predictions has not been implemented for this estimator "
                f"of type '{estimator.__class__.__name__}' in `sktime`. When "
                "this is implemented, it will be enabled by default in pycaret."
            )

        return resid

    def get_insample_predictions(
        self, estimator: BaseForecaster
    ) -> Optional[pd.Series]:
        """Returns the insample predictions for the estimator by appropriately
        taking the entire pipeline into consideration.

        Parameters
        ----------
        estimator : BaseForecaster
            sktime compatible model (without the pipeline). i.e. last step of
            the pipeline TransformedTargetForecaster

        Returns
        -------
        Optional[pd.Series]
            Insample predictions. `None` if estimator does not support insample predictions

        References
        ----------
        # https://github.com/alan-turing-institute/sktime/issues/1105#issuecomment-932216820
        # https://github.com/alan-turing-institute/sktime/blob/87bdf36dbc0990f29942eb6f7fa56a8e6c5fa7b7/sktime/forecasting/base/_base.py#L699
        """
        insample_predictions = None

        y, X = self._get_y_X_used_for_training(estimator)
        fh = ForecastingHorizon(y.index, is_relative=False)
        try:
            insample_predictions = self.predict_model(estimator, fh=fh, X=X)
        except NotImplementedError as exception:
            self.logger.warning(exception)
            print(
                "In sample predictions has not been implemented for this estimator "
                f"of type '{estimator.__class__.__name__}' in `sktime`. When "
                "this is implemented, it will be enabled by default in pycaret."
            )

        return insample_predictions

    def _check_and_clean_resid(self, resid: pd.Series) -> pd.Series:
        """Checks to see if the residuals matches one of the train set or
        full dataset. If it does, it returns the residuals without the NA values.

        Parameters
        ----------
        resid : pd.Series
            Residuals from an estimator

        Returns
        -------
        pd.Series
            Cleaned Residuals

        Raises
        ------
        ValueError
          If any one of these 3 conditions are satisfied:
            1. If residual length matches the length of train set but indices do not
            2. If residual length matches the length of full data set but indices do not
            3. If residual length does not match either train OR full dataset
        """
        y_train = self._get_y_data(split="train")
        y_all = self._get_y_data(split="all")

        if len(resid.index) == len(y_train.index):
            if np.all(resid.index != y_train.index):
                raise ValueError(
                    "Residuals match the length of the train set, but indices do not match up..."
                )
        elif len(resid.index) == len(y_all.index):
            if np.all(resid.index != y_all.index):
                raise ValueError(
                    "Residuals match the length of the full data set, but indices do not match up..."
                )
        else:
            raise ValueError(
                "Residuals time points do not match either test set or full dataset."
            )
        resid.dropna(inplace=True)
        return resid

    def get_additional_scorer_kwargs(self) -> Dict[str, Any]:
        """Returns additional kwargs required by some scorers (such as MASE).

        NOTE: These are kwargs that are experiment specific (can only be derived
        from the experiment), e.g. `sp` and not fold specific like `y_train`. In
        other words, these kwargs are applicable to all folds. Fold specific kwargs
        such as `y_train`, `lower`, `upper`, etc. must be updated dynamically.

        Returns
        -------
        Dict[str, Any]
            Additional kwargs to pass to scorers
        """
        additional_scorer_kwargs = {"sp": self.primary_sp_to_use}
        return additional_scorer_kwargs


class TimeSeriesExperiment(TSForecastingExperiment):
    def __init__(self) -> None:
        msg = (
            "DeprecationWarning: TimeSeriesExperiment class will be removed in "
            "a future release. Please import the following instead. \n"
            ">>> from pycaret.time_series import TSForecastingExperiment"
        )
        warnings.warn(msg, DeprecationWarning)
        print(msg)
        super().__init__()


class DummyForecaster(BaseForecaster):
    """Dummy Forecaster for initial pycaret pipeline"""

    _tags = {
        "scitype:y": "univariate",  # which y are fine? univariate/multivariate/both
        "ignores-exogeneous-X": False,  # does estimator use the exogenous X?
        "handles-missing-data": False,  # can estimator handle missing data?
        "y_inner_mtype": "pd.Series",  # which types do _fit, _predict, assume for y?
        "X_inner_mtype": "pd.DataFrame",  # which types do _fit, _predict, assume for X?
        "requires-fh-in-fit": False,  # is forecasting horizon already required in fit?
        "X-y-must-have-same-index": True,  # can estimator handle different X/y index?
        "enforce-index-type": None,  # index type that needs to be enforced in X/y
        "capability:pred_int": False,
    }

    def _fit(self, y, X=None, fh=None):
        self._fh_len = None
        if fh is not None:
            self._fh_len = len(fh)
        self._is_fitted = True
        return self

    def _predict(self, fh=None, X=None, return_pred_int=False, alpha=DEFAULT_ALPHA):
        self.check_is_fitted()
        if fh is not None:
            preds = pd.Series([-99_999] * len(fh))
        elif self._fh_len is not None:
            # fh seen during fit
            preds = pd.Series([-99_999] * self._fh_len)
        else:
            raise ValueError(
                f"{type(self).__name__}: `fh` is unknown. Unable to make predictions."
            )

        return preds