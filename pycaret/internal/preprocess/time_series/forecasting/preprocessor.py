from typing import Union, Optional

from sklearn.preprocessing import (
    StandardScaler,
    MinMaxScaler,
    MaxAbsScaler,
    RobustScaler,
)

from sktime.transformations.series.compose import ColumnwiseTransformer
from sktime.transformations.series.adapt import TabularToSeriesAdaptor
from sktime.transformations.series.impute import Imputer
from sktime.transformations.series.boxcox import BoxCoxTransformer, LogTransformer
from sktime.transformations.series.exponent import ExponentTransformer, SqrtTransformer
from sktime.transformations.series.cos import CosineTransformer
from sktime.transformations.series.scaledlogit import ScaledLogitTransformer

from pycaret.utils.time_series import TSApproachTypes, TSExogenousPresent


class TSForecastingPreprocessor:
    """Class for preprocessing Time Series Forecasting Experiments."""

    def __init__(self):
        # Initialize empty steps ----
        self.pipe_steps_target = []
        self.pipe_steps_exogenous = []
        # used for transformation arguments/parameters
        self.transform_target_kwargs: Optional[dict]=None
        self.transform_exogenous_kwargs: Optional[dict]=None,

    def _imputation(
        self,
        numeric_imputation_target: Optional[Union[str, int, float]],
        numeric_imputation_exogenous: Optional[Union[str, int, float]],
        exogenous_present: bool,
    ):
        #### Impute target ----
        if numeric_imputation_target is not None:
            self._add_imputation_steps(
                numeric_imputation=numeric_imputation_target, target=True
            )

        #### Impute Exogenous ----
        # Only add exogenous pipeline steps if exogenous variables are present.
        if (
            exogenous_present == TSExogenousPresent.YES
            and numeric_imputation_exogenous is not None
        ):
            self._add_imputation_steps(
                numeric_imputation=numeric_imputation_exogenous, target=False
            )

    def _add_imputation_steps(
        self, numeric_imputation: Union[str, int, float], target: bool = True
    ):
        """Perform numeric imputation of missing values.

        Parameters
        ----------
        numeric_imputation : Union[str, int, float]
            The method to be used for imputation.
            If str, then passed as is to the underlying `sktime` imputer.
            Allowed values are:
                "drift", "linear", "nearest", "mean", "median", "backfill",
                "bfill", "pad", "ffill", "random"
            If int or float, imputation method is set to "constant" with the given value.
        target : bool, optional
            If True, imputation is added to the target variable steps
            If False, imputation is added to the exogenous variable steps,
            by default True

        Raises
        ------
        ValueError
            (1) `numeric_imputation` is of type str but not of one of the allowed values.
            (2) `numeric_imputation` is not of one of the allowed types.
        """
        type_ = "Target" if target else "Exogenous"
        self.logger.info(f"Set up imputation for {type_} variable(s).")

        if isinstance(numeric_imputation, str):
            allowed_values = [
                "drift",
                "linear",
                "nearest",
                "mean",
                "median",
                "backfill",
                "bfill",
                "pad",
                "ffill",
                "random",
            ]
            if numeric_imputation not in allowed_values:
                raise ValueError(
                    f"{type_} Imputation Type '{numeric_imputation}' not allowed."
                )
            num_estimator = Imputer(method=numeric_imputation, random_state=self.seed)
        elif isinstance(numeric_imputation, (int, float)):
            num_estimator = Imputer(method="constant", value=numeric_imputation)
        else:
            raise ValueError(
                f"{type_} Imputation Type '{type(numeric_imputation)}' is not of allowed type."
            )

        if target:
            self.pipe_steps_target.extend([("numerical_imputer", num_estimator)])
        else:
            self.pipe_steps_exogenous.extend([("numerical_imputer", num_estimator)])

    def _transformation(
        self,
        transform_target: Optional[Union[str, int, float]],
        transform_exogenous: Optional[Union[str, int, float]],
        exogenous_present: bool,
    ):
        #### Impute target ----
        if transform_target is not None:
            self._add_transformation_steps(transform=transform_target, target=True)

        #### Impute Exogenous ----
        # Only add exogenous pipeline steps if exogenous variables are present.
        if (
            exogenous_present == TSExogenousPresent.YES
            and transform_exogenous is not None
        ):
            self._add_transformation_steps(transform=transform_exogenous, target=False)

    def _add_transformation_steps(self, transform: str, target: bool = True):
        """Power transform the data to be more Gaussian-like.

        Parameters
        ----------
        transform : str
            The method to be used for transformation. Allowed values and
            corresponding transformers are:
                "box-cox": BoxCoxTransformer(),
                "log": LogTransformer(),
                "sqrt": SqrtTransformer(),
                "exp": ExponentTransformer(),
                "cos": CosineTransformer(),
                "scaledlogit" : ScaledLogitTransformer(),
        target : bool, optional
            If True, transformation is added to the target variable steps
            If False, transformation is added to the exogenous variable steps,
            by default True

        Raises
        ------
        ValueError
            (1) `transform` is not of one of the allowed values.
            (2) `transform` is not of one of the allowed types.
        """
        type_ = "Target" if target else "Exogenous"
        self.logger.info(f"Set up transformation for {type_} variable(s).")

        if isinstance(transform, str):
            transform_dict = {
                "box-cox": BoxCoxTransformer,
                "log": LogTransformer,
                "sqrt": SqrtTransformer,
                "exp": ExponentTransformer,
                "cos": CosineTransformer,
                "scaledlogit" : ScaledLogitTransformer

            }

            if transform not in transform_dict:
                raise ValueError(
                    f"{type_} transformaton method '{transform}' not allowed."
                )

        else:
            raise ValueError(
                f"{type_} transformaton method '{type(transform)}' is not of allowed type (str)."
            )

        if target:
            if self.transform_target_kwargs:
                trans_kwargs: dict = self.transform_exogenous_kwargs
                # apply kwargs to transformer constuctor/__init__
            else:
                # no kwargs to use
                trans_kwargs = {} 
            target_transformer = transform_dict[transform](**trans_kwargs)
            self.pipe_steps_target.extend([("transformer", target_transformer)])
        else:
            if self.transform_exogenous_kwargs:
                trans_kwargs: dict = self.transform_exogenous_kwargs
            else:
                # no kwargs to use
                trans_kwargs = {} 
            transformer = transform_dict[transform](**trans_kwargs)
            exog_transformer = ColumnwiseTransformer(transformer)
            self.pipe_steps_exogenous.extend([("transformer", exog_transformer)])

    def _scaling(
        self,
        scale_target: Optional[Union[str, int, float]],
        scale_exogenous: Optional[Union[str, int, float]],
        exogenous_present: bool,
    ):
        #### Scale target ----
        if scale_target:
            self._add_scaling_steps(scale=scale_target, target=True)

        #### Scale Exogenous ----
        # Only add exogenous pipeline steps if exogenous variables are present.
        if exogenous_present == TSExogenousPresent.YES and scale_exogenous is not None:
            self._add_scaling_steps(scale=scale_exogenous, target=False)

    def _add_scaling_steps(self, scale: str, target: bool = True):
        """Scale the data.

        Parameters
        ----------
        scale : str
            The method to be used for scaling. Allowed values and corresponding scalers are:
                "zscore": StandardScaler(),
                "minmax": MinMaxScaler(),
                "maxabs": MaxAbsScaler(),
                "robust": RobustScaler(),
        target : bool, optional
            If True, scaling is added to the target variable steps
            If False, scaling is added to the exogenous variable steps,
            by default True

        Raises
        ------
        ValueError
            (1) `scale` is not of one of the allowed values.
            (2) `scale` is not of one of the allowed types.
        """
        type_ = "Target" if target else "Exogenous"
        self.logger.info(f"Set up scaling for {type_} variable(s).")

        if isinstance(scale, str):
            scale_dict = {
                "zscore": StandardScaler(),
                "minmax": MinMaxScaler(),
                "maxabs": MaxAbsScaler(),
                "robust": RobustScaler(),
            }

            if scale not in scale_dict:
                raise ValueError(f"{type_} scale method '{scale}' not allowed.")

            scaler = TabularToSeriesAdaptor(scale_dict[scale])

        else:
            raise ValueError(
                f"{type_} transformaton method '{type(scale)}' is not of allowed type."
            )

        if target:
            self.pipe_steps_target.extend([("scaler", scaler)])
        else:
            self.pipe_steps_exogenous.extend([("scaler", scaler)])

    # def _feature_selection(
    #     self,
    #     feature_selection_method,
    #     feature_selection_estimator,
    #     n_features_to_select,
    # ):
    #     """Select relevant features."""
    #     self.logger.info("Set up feature selection.")
    #     # TODO: Maybe implement https://github.com/pycaret/pycaret/issues/2230
    #     # self.pipeline.steps.append(("feature_selection", feature_selector))

    # def _add_custom_pipeline(self, custom_pipeline):
    #     """Add custom transformers to the pipeline."""
    #     self.logger.info("Set up custom pipeline.")
    #     for name, estimator in normalize_custom_transformers(custom_pipeline):
    #         self.pipeline.steps.append((name, TransfomerWrapper(estimator)))
