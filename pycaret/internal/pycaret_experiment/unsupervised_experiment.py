from pycaret.internal.pycaret_experiment.utils import highlight_setup, MLUsecase
from pycaret.internal.pycaret_experiment.tabular_experiment import _TabularExperiment
from pycaret.internal.pipeline import (
    estimator_pipeline,
    get_pipeline_fit_kwargs,
)
from pycaret.internal.utils import infer_ml_usecase
import pycaret.internal.patches.sklearn
import pycaret.internal.patches.yellowbrick
from pycaret.internal.logging import get_logger
from pycaret.internal.Display import Display
from pycaret.internal.distributions import *
from pycaret.internal.validation import *
import pycaret.internal.preprocess
import pycaret.internal.persistence
import pandas as pd  # type ignore
from pandas.io.formats.style import Styler
import numpy as np  # type: ignore
import os
import datetime
import time
import gc
from sklearn.base import clone  # type: ignore
from sklearn.preprocessing import LabelEncoder
from typing import List, Tuple, Any, Union, Optional, Dict
import warnings
from IPython.utils import io
import traceback
import plotly.express as px  # type: ignore
import plotly.graph_objects as go  # type: ignore
import logging


warnings.filterwarnings("ignore")
LOGGER = get_logger()


class _UnsupervisedExperiment(_TabularExperiment):
    def __init__(self) -> None:
        super().__init__()
        self.variable_keys = self.variable_keys.union({"X"})
        return

    def _get_setup_display(self, **kwargs) -> Styler:
        # define highlight function for function grid to display

        functions = pd.DataFrame(
            [
                ["session_id", self.seed],
                ["Original Data", self.data_before_preprocess.shape],
                ["Missing Values", kwargs["missing_flag"]],
                ["Numeric Features", str(kwargs["float_type"])],
                ["Categorical Features", str(kwargs["cat_type"])],
            ]
            + (
                [
                    ["Ordinal Features", kwargs["ordinal_features_grid"]],
                    [
                        "High Cardinality Features",
                        kwargs["high_cardinality_features_grid"],
                    ],
                    ["High Cardinality Method", kwargs["high_cardinality_method_grid"]],
                ]
                if self.preprocess
                else []
            )
            + [
                ["Transformed Data", self.X.shape],
                ["CPU Jobs", self.n_jobs_param],
                ["Use GPU", self.gpu_param],
                ["Log Experiment", self.logging_param],
                ["Experiment Name", self.exp_name_log],
                ["USI", self.USI],
            ]
            + (
                [
                    ["Imputation Type", kwargs["imputation_type"]],
                    [
                        "Iterative Imputation Iteration",
                        self.iterative_imputation_iters_param
                        if kwargs["imputation_type"] == "iterative"
                        else "None",
                    ],
                    ["Numeric Imputer", kwargs["numeric_imputation"]],
                    [
                        "Iterative Imputation Numeric Model",
                        kwargs["imputation_regressor_name"]
                        if kwargs["imputation_type"] == "iterative"
                        else "None",
                    ],
                    ["Categorical Imputer", kwargs["categorical_imputation"]],
                    [
                        "Iterative Imputation Categorical Model",
                        kwargs["imputation_classifier_name"]
                        if kwargs["imputation_type"] == "iterative"
                        else "None",
                    ],
                    [
                        "Unknown Categoricals Handling",
                        kwargs["unknown_categorical_method_grid"],
                    ],
                    ["Normalize", kwargs["normalize"]],
                    ["Normalize Method", kwargs["normalize_grid"]],
                    ["Transformation", kwargs["transformation"]],
                    ["Transformation Method", kwargs["transformation_grid"]],
                    ["PCA", kwargs["pca"]],
                    ["PCA Method", kwargs["pca_method_grid"]],
                    ["PCA Components", kwargs["pca_components_grid"]],
                    ["Ignore Low Variance", kwargs["ignore_low_variance"]],
                    ["Combine Rare Levels", kwargs["combine_rare_levels"]],
                    ["Rare Level Threshold", kwargs["rare_level_threshold_grid"]],
                    ["Numeric Binning", kwargs["numeric_bin_grid"]],
                    ["Remove Outliers", kwargs["remove_outliers"]],
                    ["Outliers Threshold", kwargs["outliers_threshold_grid"]],
                    [
                        "Remove Perfect Collinearity",
                        kwargs["remove_perfect_collinearity"],
                    ],
                    ["Remove Multicollinearity", kwargs["remove_multicollinearity"]],
                    [
                        "Multicollinearity Threshold",
                        kwargs["multicollinearity_threshold_grid"],
                    ],
                    [
                        "Columns Removed Due to Multicollinearity",
                        kwargs["multicollinearity_removed_columns"],
                    ],
                    ["Clustering", kwargs["create_clusters"]],
                    ["Clustering Iteration", kwargs["cluster_iter_grid"]],
                    ["Polynomial Features", kwargs["polynomial_features"]],
                    ["Polynomial Degree", kwargs["polynomial_degree_grid"]],
                    ["Trignometry Features", kwargs["trigonometry_features"]],
                    ["Polynomial Threshold", kwargs["polynomial_threshold_grid"]],
                    ["Group Features", kwargs["group_features_grid"]],
                    ["Feature Selection", kwargs["feature_selection"]],
                    ["Feature Selection Method", kwargs["feature_selection_method"]],
                    [
                        "Features Selection Threshold",
                        kwargs["feature_selection_threshold_grid"],
                    ],
                    ["Feature Interaction", kwargs["feature_interaction"]],
                    ["Feature Ratio", kwargs["feature_ratio"]],
                    ["Interaction Threshold", kwargs["interaction_threshold_grid"]],
                ]
                if self.preprocess
                else []
            ),
            columns=["Description", "Value"],
        )
        return functions.style.apply(highlight_setup)

    def _calculate_metrics(self, X, labels, ground_truth=None, ml_usecase=None) -> dict:
        """
        Calculate all metrics in _all_metrics.
        """
        from pycaret.internal.utils import calculate_unsupervised_metrics

        if ml_usecase is None:
            ml_usecase = self._ml_usecase

        try:
            return calculate_unsupervised_metrics(
                metrics=self._all_metrics, X=X, labels=labels, ground_truth=ground_truth
            )
        except Exception:
            if ml_usecase == MLUsecase.CLUSTERING:
                metrics = pycaret.containers.metrics.clustering.get_all_metric_containers(
                    self.variables, True
                )
            return calculate_unsupervised_metrics(
                metrics=metrics,  # type: ignore
                X=X,
                labels=labels,
                ground_truth=ground_truth,
            )

    def _is_unsupervised(self) -> bool:
        return True

    def _split_data(
        self,
        X_before_preprocess,
        y_before_preprocess,
        target,
        train_data,
        test_data,
        train_size,
        data_split_shuffle,
        dtypes,
        display: Display,
        fh=None,
    ) -> None:
        display.move_progress()
        self.X = self.prep_pipe.fit_transform(train_data).drop(target, axis=1)
        self.X_train = self.X

    def _set_up_mlflow(
        self, functions, runtime, log_profile, profile_kwargs, log_data, display,
    ) -> None:
        functions_styler = functions
        if isinstance(functions, Styler):
            functions = functions.data
        # log into experiment
        self.experiment__.append(("Setup Config", functions))
        self.experiment__.append(("Transformed Data", self.X))
        self.experiment__.append(("Transformation Pipeline", self.prep_pipe))

        if self.logging_param:

            self.logger.info("Logging experiment in MLFlow")

            import mlflow

            try:
                mlflow.create_experiment(self.exp_name_log)
            except Exception:
                self.logger.warning("Couldn't create mlflow experiment. Exception:")
                self.logger.warning(traceback.format_exc())

            # mlflow logging
            mlflow.set_experiment(self.exp_name_log)

            run_name_ = f"Session Initialized {self.USI}"

            with mlflow.start_run(run_name=run_name_) as run:

                # Get active run to log as tag
                RunID = mlflow.active_run().info.run_id

                k = functions.copy()
                k.set_index("Description", drop=True, inplace=True)
                kdict = k.to_dict()
                params = kdict.get("Value")
                mlflow.log_params(params)

                # set tag of compare_models
                mlflow.set_tag("Source", "setup")

                import secrets

                URI = secrets.token_hex(nbytes=4)
                mlflow.set_tag("URI", URI)
                mlflow.set_tag("USI", self.USI)
                mlflow.set_tag("Run Time", runtime)
                mlflow.set_tag("Run ID", RunID)

                # Log the transformation pipeline
                self.logger.info(
                    "SubProcess save_model() called =================================="
                )
                self.save_model(
                    self.prep_pipe, "Transformation Pipeline", verbose=False
                )
                self.logger.info(
                    "SubProcess save_model() end =================================="
                )
                mlflow.log_artifact("Transformation Pipeline.pkl")
                os.remove("Transformation Pipeline.pkl")

                # Log pandas profile
                if log_profile:
                    import pandas_profiling

                    pf = pandas_profiling.ProfileReport(
                        self.data_before_preprocess, **profile_kwargs
                    )
                    pf.to_file("Data Profile.html")
                    mlflow.log_artifact("Data Profile.html")
                    os.remove("Data Profile.html")
                    display.display(functions_styler, clear=True)

                # Log training and testing set
                if log_data:
                    self.X.to_csv("Dataset.csv")
                    mlflow.log_artifact("Dataset.csv")
                    os.remove("Dataset.csv")
        return

    def setup(
        self,
        data: pd.DataFrame,
        train_size: float = 0.7,
        test_data: Optional[pd.DataFrame] = None,
        preprocess: bool = True,
        imputation_type: str = "simple",
        iterative_imputation_iters: int = 5,
        categorical_features: Optional[List[str]] = None,
        categorical_imputation: str = "mode",
        categorical_iterative_imputer: Union[str, Any] = "lightgbm",
        ordinal_features: Optional[Dict[str, list]] = None,
        high_cardinality_features: Optional[List[str]] = None,
        high_cardinality_method: str = "frequency",
        numeric_features: Optional[List[str]] = None,
        numeric_imputation: str = "mean",  # method 'zero' added in pycaret==2.1
        numeric_iterative_imputer: Union[str, Any] = "lightgbm",
        date_features: Optional[List[str]] = None,
        ignore_features: Optional[List[str]] = None,
        normalize: bool = False,
        normalize_method: str = "zscore",
        transformation: bool = False,
        transformation_method: str = "yeo-johnson",
        handle_unknown_categorical: bool = True,
        unknown_categorical_method: str = "least_frequent",
        pca: bool = False,
        pca_method: str = "linear",
        pca_components: Union[int, float] = 1.0,
        ignore_low_variance: bool = False,
        combine_rare_levels: bool = False,
        rare_level_threshold: float = 0.10,
        bin_numeric_features: Optional[List[str]] = None,
        remove_multicollinearity: bool = False,
        multicollinearity_threshold: float = 0.9,
        remove_perfect_collinearity: bool = True,
        group_features: Optional[List[str]] = None,
        group_names: Optional[List[str]] = None,
        n_jobs: Optional[int] = -1,
        use_gpu: bool = False,  # added in pycaret==2.1
        custom_pipeline: Union[
            Any, Tuple[str, Any], List[Any], List[Tuple[str, Any]]
        ] = None,
        html: bool = True,
        session_id: Optional[int] = None,
        system_log: Union[bool, logging.Logger] = True,
        log_experiment: bool = False,
        experiment_name: Optional[str] = None,
        log_plots: Union[bool, list] = False,
        log_profile: bool = False,
        log_data: bool = False,
        silent: bool = False,
        verbose: bool = True,
        profile: bool = False,
        profile_kwargs: Dict[str, Any] = None,
        display: Optional[Display] = None,
    ):
        return super().setup(
            data=data,
            target=None,
            train_size=train_size,
            test_data=test_data,
            preprocess=preprocess,
            imputation_type=imputation_type,
            iterative_imputation_iters=iterative_imputation_iters,
            categorical_features=categorical_features,
            categorical_imputation=categorical_imputation,
            categorical_iterative_imputer=categorical_iterative_imputer,
            ordinal_features=ordinal_features,
            high_cardinality_features=high_cardinality_features,
            high_cardinality_method=high_cardinality_method,
            numeric_features=numeric_features,
            numeric_imputation=numeric_imputation,
            numeric_iterative_imputer=numeric_iterative_imputer,
            date_features=date_features,
            ignore_features=ignore_features,
            normalize=normalize,
            normalize_method=normalize_method,
            transformation=transformation,
            transformation_method=transformation_method,
            handle_unknown_categorical=handle_unknown_categorical,
            unknown_categorical_method=unknown_categorical_method,
            pca=pca,
            pca_method=pca_method,
            pca_components=pca_components,
            ignore_low_variance=ignore_low_variance,
            combine_rare_levels=combine_rare_levels,
            rare_level_threshold=rare_level_threshold,
            bin_numeric_features=bin_numeric_features,
            remove_outliers=False,
            remove_multicollinearity=remove_multicollinearity,
            multicollinearity_threshold=multicollinearity_threshold,
            remove_perfect_collinearity=remove_perfect_collinearity,
            create_clusters=False,
            polynomial_features=False,
            trigonometry_features=False,
            group_features=group_features,
            group_names=group_names,
            feature_selection=False,
            feature_interaction=False,
            feature_ratio=False,
            fix_imbalance=False,
            data_split_shuffle=False,
            data_split_stratify=False,
            n_jobs=n_jobs,
            use_gpu=use_gpu,
            custom_pipeline=custom_pipeline,
            html=html,
            session_id=session_id,
            system_log=system_log,
            log_experiment=log_experiment,
            experiment_name=experiment_name,
            log_plots=log_plots,
            log_profile=log_profile,
            log_data=log_data,
            silent=silent,
            verbose=verbose,
            profile=profile,
            profile_kwargs=profile_kwargs,
            display=display,
        )

    def tune_model(
        self,
        model,
        supervised_target: str,
        supervised_type: Optional[str] = None,
        supervised_estimator: Union[str, Any] = "lr",
        optimize: Optional[str] = None,
        custom_grid: Optional[List[int]] = None,
        fold: Optional[Union[int, Any]] = None,
        groups: Optional[Union[str, Any]] = None,
        ground_truth: Optional[str] = None,
        method: str = "drop",
        fit_kwargs: Optional[dict] = None,
        round: int = 4,
        verbose: bool = True,
        display: Optional[Display] = None,
        **kwargs,
    ):

        function_params_str = ", ".join([f"{k}={v}" for k, v in locals().items()])

        self.logger.info("Initializing tune_model()")
        self.logger.info(f"tune_model({function_params_str})")

        self.logger.info("Checking exceptions")

        # run_time
        runtime_start = time.time()

        if not fit_kwargs:
            fit_kwargs = {}

        if supervised_target not in self.data_before_preprocess.columns:
            raise ValueError(
                f"{supervised_target} is not present as a column in the dataset."
            )

        warnings.filterwarnings("ignore")

        np.random.seed(self.seed)

        cols_to_drop = [x for x in self.X.columns if x.startswith(supervised_target)]
        data_X = self.X.drop(cols_to_drop, axis=1)
        data_y = self.data_before_preprocess[[supervised_target]]
        if data_y.dtypes[0] not in [int, float, bool]:
            data_y[supervised_target] = LabelEncoder().fit_transform(
                data_y[supervised_target]
            )
        data_y = data_y[supervised_target]

        temp_globals = self.variables
        temp_globals["y_train"] = data_y

        if supervised_type is None:
            supervised_type, _ = infer_ml_usecase(data_y)
            self.logger.info(f"supervised_type inferred as {supervised_type}")

        if supervised_type == "classification":
            metrics = pycaret.containers.metrics.classification.get_all_metric_containers(
                temp_globals, raise_errors=True
            )
            available_estimators = pycaret.containers.models.classification.get_all_model_containers(
                temp_globals, raise_errors=True
            )
            ml_usecase = MLUsecase.CLASSIFICATION
        elif supervised_type == "regression":
            metrics = pycaret.containers.metrics.regression.get_all_metric_containers(
                temp_globals, raise_errors=True
            )
            available_estimators = pycaret.containers.models.regression.get_all_model_containers(
                temp_globals, raise_errors=True
            )
            ml_usecase = MLUsecase.REGRESSION
        else:
            raise ValueError(
                "supervised_type parameter must be either 'classification' or 'regression'."
            )

        fold = self._get_cv_splitter(fold, ml_usecase)

        if isinstance(supervised_estimator, str):
            if supervised_estimator in available_estimators:
                estimator_definition = available_estimators[supervised_estimator]
                estimator_args = estimator_definition.args
                estimator_args = {**estimator_args}
                supervised_estimator = estimator_definition.class_def(**estimator_args)
            else:
                raise ValueError(
                    f"Unknown supervised_estimator {supervised_estimator}."
                )
        else:
            self.logger.info("Declaring custom model")

            supervised_estimator = clone(supervised_estimator)

        supervised_estimator_name = self._get_model_name(
            supervised_estimator, models=available_estimators
        )

        if optimize is None:
            optimize = "Accuracy" if supervised_type == "classification" else "R2"
        optimize = self._get_metric_by_name_or_id(optimize, metrics=metrics)
        if optimize is None:
            raise ValueError(
                "Optimize method not supported. See docstring for list of available parameters."
            )

        if custom_grid is not None and not isinstance(custom_grid, list):
            raise ValueError("custom_grid parameter must be a list.")

        # checking round parameter
        if type(round) is not int:
            raise TypeError("Round parameter only accepts integer value.")

        # checking verbose parameter
        if type(verbose) is not bool:
            raise TypeError(
                "Verbose parameter can only take argument as True or False."
            )

        if custom_grid is None:
            if self._ml_usecase == MLUsecase.CLUSTERING:
                param_grid = [2, 4, 5, 6, 8, 10, 14, 18, 25, 30, 40]
            else:
                param_grid = [
                    0.01,
                    0.02,
                    0.03,
                    0.04,
                    0.05,
                    0.06,
                    0.07,
                    0.08,
                    0.09,
                    0.10,
                ]
        else:
            param_grid = custom_grid
            try:
                param_grid.remove(0)
            except ValueError:
                pass
        param_grid.sort()

        if not display:
            progress_args = {"max": len(param_grid) * 3 + (len(param_grid) + 1) * 4}
            master_display_columns = None
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

        unsupervised_models = {}
        unsupervised_models_results = {}
        unsupervised_grids = {0: data_X}

        self.logger.info("Fitting unsupervised models")

        for k in param_grid:
            if self._ml_usecase == MLUsecase.CLUSTERING:
                try:
                    new_model, _ = self.create_model(
                        model,
                        num_clusters=k,
                        X_data=data_X,
                        display=display,
                        system=False,
                        ground_truth=ground_truth,
                        round=round,
                        fit_kwargs=fit_kwargs,
                        raise_num_clusters=True,
                        **kwargs,
                    )
                except ValueError:
                    raise ValueError(
                        f"Model {model} cannot be used in this function as its number of clusters cannot be set (n_clusters parameter required)."
                    )
            else:
                new_model, _ = self.create_model(
                    model,
                    fraction=k,
                    X_data=data_X,
                    display=display,
                    system=False,
                    ground_truth=ground_truth,
                    round=round,
                    fit_kwargs=fit_kwargs,
                    **kwargs,
                )
            unsupervised_models_results[k] = self.pull(pop=True)
            unsupervised_models[k] = new_model
            unsupervised_grids[k] = (
                self.assign_model(new_model, verbose=False, transformation=True)
                .reset_index(drop=True)
                .drop(cols_to_drop, axis=1)
            )
            if self._ml_usecase == MLUsecase.CLUSTERING:
                unsupervised_grids[k] = pd.get_dummies(
                    unsupervised_grids[k], columns=["Cluster"],
                )
            elif method == "drop":
                unsupervised_grids[k] = unsupervised_grids[k][
                    unsupervised_grids[k]["Anomaly"] == 0
                ].drop(["Anomaly", "Anomaly_Score"], axis=1)

        results = {}

        self.logger.info("Fitting supervised estimator")

        for k, v in unsupervised_grids.items():
            self.create_model(
                supervised_estimator,
                fold=fold,
                display=display,
                system=False,
                X_train_data=v,
                y_train_data=data_y[data_y.index.isin(v.index)],
                metrics=metrics,
                groups=groups,
                round=round,
                refit=False,
            )
            results[k] = self.pull(pop=True).loc["Mean"]
            display.move_progress()

        self.logger.info("Compiling results")

        results = pd.DataFrame(results).T

        greater_is_worse_columns = {
            v.display_name for k, v in metrics.items() if not v.greater_is_better
        }

        best_model_idx = (
            results.drop(0)
            .sort_values(
                by=optimize.display_name, ascending=optimize in greater_is_worse_columns
            )
            .index[0]
        )

        def highlight_max(s):
            to_highlight = s == s.max()
            return ["background-color: yellow" if v else "" for v in to_highlight]

        def highlight_min(s):
            to_highlight = s == s.min()
            return ["background-color: yellow" if v else "" for v in to_highlight]

        results = results.style.apply(
            highlight_max,
            subset=[x for x in results.columns if x not in greater_is_worse_columns],
        ).apply(
            highlight_min,
            subset=[x for x in results.columns if x in greater_is_worse_columns],
        )

        # end runtime
        runtime_end = time.time()
        runtime = np.array(runtime_end - runtime_start).round(2)

        if self._ml_usecase == MLUsecase.CLUSTERING:
            best_model, best_model_fit_time = self.create_model(
                unsupervised_models[best_model_idx],
                num_clusters=best_model_idx,
                system=False,
                round=round,
                ground_truth=ground_truth,
                fit_kwargs=fit_kwargs,
                display=display,
                **kwargs,
            )
        else:
            best_model, best_model_fit_time = self.create_model(
                unsupervised_models[best_model_idx],
                fraction=best_model_idx,
                system=False,
                round=round,
                fit_kwargs=fit_kwargs,
                display=display,
                **kwargs,
            )
        best_model_results = self.pull(pop=True)

        if self.logging_param:

            metrics_log = {k: v[0] for k, v in best_model_results.items()}

            try:
                self._mlflow_log_model(
                    model=model,
                    model_results=None,
                    score_dict=metrics_log,
                    source="tune_model",
                    runtime=runtime,
                    model_fit_time=best_model_fit_time,
                    _prep_pipe=self.prep_pipe,
                    log_plots=self.log_plots_param,
                    display=display,
                )
            except Exception:
                self.logger.error(
                    f"_mlflow_log_model() for {model} raised an exception:"
                )
                self.logger.error(traceback.format_exc())

        results = results.set_precision(round)
        self.display_container.append(results)

        display.display(results, clear=True)

        if self.html_param and verbose:
            self.logger.info("Rendering Visual")
            plot_df = results.data.drop(
                [x for x in results.columns if x != optimize.display_name], axis=1
            )

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=plot_df.index,
                    y=plot_df[optimize.display_name],
                    mode="lines+markers",
                    name=optimize.display_name,
                )
            )
            msg = (
                "Number of Clusters"
                if self._ml_usecase == MLUsecase.CLUSTERING
                else "Anomaly Fraction"
            )
            title = f"{supervised_estimator_name} Metrics and {msg} by {self._get_model_name(best_model)}"
            fig.update_layout(
                plot_bgcolor="rgb(245,245,245)",
                title={
                    "text": title,
                    "y": 0.95,
                    "x": 0.45,
                    "xanchor": "center",
                    "yanchor": "top",
                },
                xaxis_title=msg,
                yaxis_title=optimize.display_name,
            )
            fig.show()
            self.logger.info("Visual Rendered Successfully")

        self.logger.info(f"master_model_container: {len(self.master_model_container)}")
        self.logger.info(f"display_container: {len(self.display_container)}")

        self.logger.info(str(best_model))
        self.logger.info(
            "tune_model() succesfully completed......................................"
        )

        gc.collect()

        return best_model

    def assign_model(
        self,
        model,
        transformation: bool = False,
        score: bool = True,
        verbose: bool = True,
    ) -> pd.DataFrame:

        """
        This function assigns each of the data point in the dataset passed during setup
        stage to one of the clusters using trained model object passed as model param.
        create_model() function must be called before using assign_model().

        This function returns a pandas.DataFrame.

        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> jewellery = get_data('jewellery')
        >>> experiment_name = setup(data = jewellery, normalize = True)
        >>> kmeans = create_model('kmeans')
        >>> kmeans_df = assign_model(kmeans)

        This will return a pandas.DataFrame with inferred clusters using trained model.

        Parameters
        ----------
        model: trained model object, default = None

        transformation: bool, default = False
            When set to True, assigned clusters are returned on transformed dataset instead
            of original dataset passed during setup().

        verbose: Boolean, default = True
            Status update is not printed when verbose is set to False.

        Returns
        -------
        pandas.DataFrame
            Returns a DataFrame with assigned clusters using a trained model.

        """

        function_params_str = ", ".join([f"{k}={v}" for k, v in locals().items()])

        self.logger.info("Initializing assign_model()")
        self.logger.info(f"assign_model({function_params_str})")

        self.logger.info("Checking exceptions")

        # checking transformation parameter
        if type(transformation) is not bool:
            raise TypeError(
                "Transformation parameter can only take argument as True or False."
            )

        # checking verbose parameter
        if type(verbose) is not bool:
            raise TypeError(
                "Verbose parameter can only take argument as True or False."
            )

        """
        error handling ends here
        """

        if is_sklearn_pipeline(model):
            model = model.steps[-1][1]

        self.logger.info("Determining Trained Model")

        name = self._get_model_name(model)

        self.logger.info(f"Trained Model : {name}")

        self.logger.info("Copying data")
        # copy data_
        if transformation:
            data = self.X.copy()
            self.logger.info(
                "Transformation parameter set to True. Assigned clusters are attached on transformed dataset."
            )
        else:
            data = self.data_before_preprocess.copy()

        # calculation labels and attaching to dataframe

        if self._ml_usecase == MLUsecase.CLUSTERING:
            labels = [f"Cluster {i}" for i in model.labels_]
            data["Cluster"] = labels
        else:
            data["Anomaly"] = model.labels_
            if score:
                data["Anomaly_Score"] = model.decision_scores_

        self.logger.info(data.shape)
        self.logger.info(
            "assign_model() succesfully completed......................................"
        )

        return data

    def predict_model(
        self, estimator, data: pd.DataFrame, ml_usecase: Optional[MLUsecase] = None,
    ) -> pd.DataFrame:
        function_params_str = ", ".join(
            [f"{k}={v}" for k, v in locals().items() if k != "data"]
        )

        self.logger.info("Initializing predict_model()")
        self.logger.info(f"predict_model({function_params_str})")

        if ml_usecase is None:
            ml_usecase = self._ml_usecase

        # copy data and model
        data_transformed = data.copy()

        # exception checking for predict param
        if hasattr(estimator, "predict"):
            pass
        else:
            raise TypeError("Model doesn't support predict parameter.")

        pred_score = None

        # predictions start here
        if is_sklearn_pipeline(estimator):
            pred = estimator.predict(data_transformed)
            if ml_usecase == MLUsecase.ANOMALY:
                pred_score = estimator.decision_function(data_transformed)
        else:
            pred = estimator.predict(self.prep_pipe.transform(data_transformed))
            if ml_usecase == MLUsecase.ANOMALY:
                pred_score = estimator.decision_function(
                    self.prep_pipe.transform(data_transformed)
                )

        if ml_usecase == MLUsecase.CLUSTERING:
            pred_list = [f"Cluster {i}" for i in pred]

            data_transformed["Cluster"] = pred_list
        else:
            data_transformed["Anomaly"] = pred
            data_transformed["Anomaly_Score"] = pred_score

        return data_transformed

    def create_model(
        self,
        estimator,
        num_clusters: int = 4,
        fraction: float = 0.05,
        ground_truth: Optional[str] = None,
        round: int = 4,
        fit_kwargs: Optional[dict] = None,
        verbose: bool = True,
        system: bool = True,
        raise_num_clusters: bool = False,
        X_data: Optional[pd.DataFrame] = None,  # added in pycaret==2.2.0
        display: Optional[Display] = None,  # added in pycaret==2.2.0
        **kwargs,
    ) -> Any:

        """
        This is an internal version of the create_model function.

        This function creates a model and scores it using Cross Validation.
        The output prints a score grid that shows Accuracy, AUC, Recall, Precision,
        F1, Kappa and MCC by fold (default = 10 Fold).

        This function returns a trained model object.

        setup() function must be called before using create_model()

        Example
        -------
        >>> from pycaret.datasets import get_data
        >>> juice = get_data('juice')
        >>> experiment_name = setup(data = juice,  target = 'Purchase')
        >>> lr = create_model('lr')

        This will create a trained Logistic Regression model.

        Parameters
        ----------
        model : string / object, default = None
            Enter ID of the models available in model library or pass an untrained model
            object consistent with fit / predict API to train and evaluate model. List of
            models available in model library (ID - Model):

            * 'kmeans' - K-Means Clustering
            * 'ap' - Affinity Propagation
            * 'meanshift' - Mean shift Clustering
            * 'sc' - Spectral Clustering
            * 'hclust' - Agglomerative Clustering
            * 'dbscan' - Density-Based Spatial Clustering
            * 'optics' - OPTICS Clustering
            * 'birch' - Birch Clustering
            * 'kmodes' - K-Modes Clustering

        num_clusters: int, default = 4
            Number of clusters to be generated with the dataset.

        ground_truth: string, default = None
            When ground_truth is provided, Homogeneity Score, Rand Index, and
            Completeness Score is evaluated and printer along with other metrics.

        round: integer, default = 4
            Number of decimal places the metrics in the score grid will be rounded to.

        fit_kwargs: dict, default = {} (empty dict)
            Dictionary of arguments passed to the fit method of the model.

        verbose: bool, default = True
            Score grid is not printed when verbose is set to False.

        system: bool, default = True
            Must remain True all times. Only to be changed by internal functions.
            If False, method will return a tuple of model and the model fit time.

        **kwargs:
            Additional keyword arguments to pass to the estimator.

        Returns
        -------
        score_grid
            A table containing the Silhouette, Calinski-Harabasz,
            Davies-Bouldin, Homogeneity Score, Rand Index, and
            Completeness Score. Last 3 are only evaluated when
            ground_truth parameter is provided.

        model
            trained model object

        Warnings
        --------
        - num_clusters not required for Affinity Propagation ('ap'), Mean shift
        clustering ('meanshift'), Density-Based Spatial Clustering ('dbscan')
        and OPTICS Clustering ('optics'). num_clusters parameter for these models
        are automatically determined.

        - When fit doesn't converge in Affinity Propagation ('ap') model, all
        datapoints are labelled as -1.

        - Noisy samples are given the label -1, when using Density-Based Spatial
        ('dbscan') or OPTICS Clustering ('optics').

        - OPTICS ('optics') clustering may take longer training times on large
        datasets.

        """

        function_params_str = ", ".join(
            [f"{k}={v}" for k, v in locals().items() if k not in ("X_data")]
        )

        self.logger.info("Initializing create_model()")
        self.logger.info(f"create_model({function_params_str})")

        self.logger.info("Checking exceptions")

        # run_time
        runtime_start = time.time()

        available_estimators = set(self._all_models_internal.keys())

        if not fit_kwargs:
            fit_kwargs = {}

        # only raise exception of estimator is of type string.
        if isinstance(estimator, str):
            if estimator not in available_estimators:
                raise ValueError(
                    f"Estimator {estimator} not available. Please see docstring for list of available estimators."
                )
        elif not hasattr(estimator, "fit"):
            raise ValueError(
                f"Estimator {estimator} does not have the required fit() method."
            )

        # checking round parameter
        if type(round) is not int:
            raise TypeError("Round parameter only accepts integer value.")

        # checking verbose parameter
        if type(verbose) is not bool:
            raise TypeError(
                "Verbose parameter can only take argument as True or False."
            )

        # checking system parameter
        if type(system) is not bool:
            raise TypeError("System parameter can only take argument as True or False.")

        # checking fraction type:
        if fraction <= 0 or fraction >= 1:
            raise TypeError(
                "Fraction parameter can only take value as float between 0 to 1."
            )

        # checking num_clusters type:
        if num_clusters <= 1:
            raise TypeError(
                "num_clusters parameter can only take value integer value greater than 1."
            )

        # check ground truth exist in data_
        if ground_truth is not None:
            if ground_truth not in self.data_before_preprocess.columns:
                raise ValueError(
                    f"ground_truth {ground_truth} doesn't exist in the dataset."
                )

        """

        ERROR HANDLING ENDS HERE

        """

        if not display:
            progress_args = {"max": 3}
            master_display_columns = [
                v.display_name for k, v in self._all_metrics.items()
            ]
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

        self.logger.info("Importing libraries")

        # general dependencies

        np.random.seed(self.seed)

        # Storing X_train and y_train in data_X and data_y parameter
        data_X = self.X if X_data is None else X_data

        """
        MONITOR UPDATE STARTS
        """
        display.update_monitor(1, "Selecting Estimator")
        display.display_monitor()
        """
        MONITOR UPDATE ENDS
        """

        self.logger.info("Importing untrained model")

        is_cblof = False

        if isinstance(estimator, str) and estimator in available_estimators:
            is_cblof = estimator == "cluster"
            model_definition = self._all_models_internal[estimator]
            model_args = model_definition.args
            model_args = {**model_args, **kwargs}
            model = model_definition.class_def(**model_args)
            full_name = model_definition.name
        else:
            self.logger.info("Declaring custom model")

            model = clone(estimator)
            model.set_params(**kwargs)

            full_name = self._get_model_name(model)

        display.update_monitor(2, full_name)
        display.display_monitor()

        if self._ml_usecase == MLUsecase.CLUSTERING:
            if raise_num_clusters:
                model.set_params(n_clusters=num_clusters)
            else:
                try:
                    model.set_params(n_clusters=num_clusters)
                except Exception:
                    pass
        else:
            model.set_params(contamination=fraction)

        # workaround for an issue with set_params in cuML
        try:
            model = clone(model)
        except Exception:
            self.logger.warning(
                f"create_model() for {model} raised an exception when cloning:"
            )
            self.logger.warning(traceback.format_exc())

        self.logger.info(f"{full_name} Imported succesfully")

        display.move_progress()

        """
        MONITOR UPDATE STARTS
        """
        if self._ml_usecase == MLUsecase.CLUSTERING:
            display.update_monitor(1, f"Fitting {num_clusters} Clusters")
        else:
            display.update_monitor(1, f"Fitting {fraction} Fraction")
        display.display_monitor()
        """
        MONITOR UPDATE ENDS
        """

        with estimator_pipeline(self._internal_pipeline, model) as pipeline_with_model:
            fit_kwargs = get_pipeline_fit_kwargs(pipeline_with_model, fit_kwargs)

            self.logger.info("Fitting Model")
            model_fit_start = time.time()
            with io.capture_output():
                if is_cblof and "n_clusters" not in kwargs:
                    try:
                        pipeline_with_model.fit(data_X, **fit_kwargs)
                    except Exception:
                        try:
                            pipeline_with_model.set_params(
                                actual_estimator__n_clusters=12
                            )
                            model_fit_start = time.time()
                            pipeline_with_model.fit(data_X, **fit_kwargs)
                        except Exception:
                            raise RuntimeError(
                                "Could not form valid cluster separation. Try a different dataset or model."
                            )
                else:
                    pipeline_with_model.fit(data_X, **fit_kwargs)
            model_fit_end = time.time()

            model_fit_time = np.array(model_fit_end - model_fit_start).round(2)

        display.move_progress()

        if ground_truth is not None:

            self.logger.info(f"ground_truth parameter set to {ground_truth}")

            gt = np.array(self.data_before_preprocess[ground_truth])
        else:
            gt = None

        if self._ml_usecase == MLUsecase.CLUSTERING:
            metrics = self._calculate_metrics(data_X, model.labels_, ground_truth=gt)
        else:
            metrics = {}

        self.logger.info(str(model))
        self.logger.info(
            "create_models() succesfully completed......................................"
        )

        runtime = time.time() - runtime_start

        # mlflow logging
        if self.logging_param and system:

            metrics_log = {k: v for k, v in metrics.items()}

            try:
                self._mlflow_log_model(
                    model=model,
                    model_results=None,
                    score_dict=metrics_log,
                    source="create_model",
                    runtime=runtime,
                    model_fit_time=model_fit_time,
                    _prep_pipe=self.prep_pipe,
                    log_plots=self.log_plots_param,
                    display=display,
                )
            except Exception:
                self.logger.error(
                    f"_mlflow_log_model() for {model} raised an exception:"
                )
                self.logger.error(traceback.format_exc())

        display.move_progress()

        self.logger.info("Uploading results into container")

        model_results = pd.DataFrame(metrics, index=[0])
        model_results = model_results.round(round)

        self.display_container.append(model_results)

        # storing results in master_model_container
        self.logger.info("Uploading model into container now")
        self.master_model_container.append(
            {"model": model, "scores": model_results, "cv": None}
        )

        if self._ml_usecase == MLUsecase.CLUSTERING:
            display.display(
                model_results, clear=system, override=False if not system else None
            )
        elif system:
            display.clear_output()

        self.logger.info(f"master_model_container: {len(self.master_model_container)}")
        self.logger.info(f"display_container: {len(self.display_container)}")

        self.logger.info(str(model))
        self.logger.info(
            "create_model() succesfully completed......................................"
        )
        gc.collect()

        if not system:
            return (model, model_fit_time)

        return model

