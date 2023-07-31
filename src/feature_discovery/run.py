import logging
import time
from typing import List, Optional

import numpy as np
import pandas as pd
import tqdm

from feature_discovery.autofeat_pipeline.autofeat import AutoFeat
from feature_discovery.config import DATA_FOLDER, RESULTS_FOLDER
from feature_discovery.experiments.dataset_object import Dataset, REGRESSION
from feature_discovery.experiments.evaluate_join_paths import evaluate_paths
from feature_discovery.experiments.init_datasets import init_datasets
from feature_discovery.experiments.result_object import Result
from feature_discovery.experiments.train_autogluon import run_auto_gluon
from feature_discovery.experiments.utils_dataset import filter_datasets
from feature_discovery.graph_processing.neo4j_transactions import export_dataset_connections, export_all_connections

logging.getLogger().setLevel(logging.WARNING)

hyper_parameters = {"RF": {}, "GBM": {}, "XGB": {}, "XT": {}}

init_datasets()


def get_base_results(dataset: Dataset):
    logging.debug(f"Base result on table {dataset.base_table_id}")

    dataframe = pd.read_csv(
        DATA_FOLDER / dataset.base_table_id,
        header=0,
        engine="python",
        encoding="utf8",
        quotechar='"',
        escapechar="\\",
    )

    features = list(dataframe.columns)

    _, results = run_auto_gluon(
        approach=Result.BASE,
        dataframe=dataframe[features],
        target_column=dataset.target_column,
        data_label=dataset.base_table_label,
        join_name=dataset.base_table_label,
        algorithms_to_run=hyper_parameters,
        problem_type=dataset.dataset_type,
    )

    # Save intermediate results
    pd.DataFrame(results).to_csv(RESULTS_FOLDER / f"{dataset.base_table_label}_base.csv", index=False)

    return results


def get_arda_results(dataset: Dataset, sample_size: int = 3000) -> List:
    from feature_discovery.arda.arda import select_arda_features_budget_join

    logging.debug(f"ARDA result on table {dataset.base_table_id}")

    start = time.time()
    (
        dataframe,
        base_table_features,
        selected_features,
        join_name,
    ) = select_arda_features_budget_join(
        base_node_id=str(dataset.base_table_id),
        target_column=dataset.target_column,
        sample_size=sample_size,
        regression=(dataset.dataset_type == REGRESSION),
    )
    end = time.time()
    logging.debug(f"X shape: {dataframe.shape}\nSelected features:\n\t{selected_features}")

    features = selected_features.copy()
    features.append(dataset.target_column)
    features.extend(base_table_features)

    logging.debug(f"Running on ARDA Feature Selection result with AutoGluon")
    start_ag = time.time()
    _, results = run_auto_gluon(
        approach=Result.ARDA,
        dataframe=dataframe[features],
        target_column=dataset.target_column,
        data_label=dataset.base_table_label,
        join_name=join_name,
        algorithms_to_run=hyper_parameters,
        problem_type=dataset.dataset_type
    )
    end_ag = time.time()
    for result in results:
        result.feature_selection_time = end - start
        result.train_time = end_ag - start_ag
        result.total_time += result.feature_selection_time
        result.total_time += result.train_time

    pd.DataFrame(results).to_csv(RESULTS_FOLDER / f"{dataset.base_table_label}_arda.csv", index=False)

    return results


def get_tfd_results(dataset: Dataset, top_k: int = 15, value_ratio: float = 0.65) -> List:
    logging.debug(f"Running on TFD (Transitive Feature Discovery) result with AutoGluon")

    start = time.time()
    bfs_traversal = AutoFeat(
        base_table_id=str(dataset.base_table_id),
        base_table_label=dataset.base_table_label,
        target_column=dataset.target_column,
        value_ratio=value_ratio,
        top_k=top_k,
        task=dataset.dataset_type
    )
    bfs_traversal.streaming_feature_selection(queue={str(dataset.base_table_id)})
    end = time.time()

    logging.debug("FINISHED TFD")

    all_results, top_k_paths = evaluate_paths(bfs_result=bfs_traversal,
                                              top_k=top_k,
                                              feat_sel_time=end - start,
                                              problem_type=dataset.dataset_type)
    logging.debug(top_k_paths)

    logging.debug("Save results ... ")
    pd.DataFrame(top_k_paths, columns=['path', 'score']).to_csv(
        f"paths_tfd_{dataset.base_table_label}_{value_ratio}.csv", index=False)

    return all_results


def get_all_results(
        value_ratio: float,
        problem_type: Optional[str],
        dataset_labels: Optional[List[str]] = None,
        results_file: str = "all_results_autogluon.csv",
):
    all_results = []
    datasets = filter_datasets(dataset_labels, problem_type)

    for dataset in tqdm.tqdm(datasets):
        result_bfs = get_tfd_results(dataset, value_ratio=value_ratio)
        all_results.extend(result_bfs)
        result_base = get_base_results(dataset)
        all_results.extend(result_base)
        result_arda = get_arda_results(dataset)
        all_results.extend(result_arda)

    pd.DataFrame(all_results).to_csv(RESULTS_FOLDER / results_file, index=False)


def get_results_tune_value_ratio_classification(datasets: List[Dataset], results_filename: str):
    all_results = []
    value_ratio_threshold = np.arange(1, 1.05, 0.05)
    for threshold in value_ratio_threshold:
        print(f"==== value_ratio = {threshold} ==== ")
        for dataset in datasets:
            print(f"\tDataset = {dataset.base_table_label} ==== ")
            result_bfs = get_tfd_results(dataset, value_ratio=threshold, top_k=15)
            all_results.extend(result_bfs)
        pd.DataFrame(all_results).to_csv(RESULTS_FOLDER / f"value_ratio_{threshold}_{results_filename}", index=False)
    pd.DataFrame(all_results).to_csv(RESULTS_FOLDER / results_filename, index=False)


def get_results_tune_k(datasets: List[Dataset], results_filename: str):
    all_results = []
    top_k = np.arange(1, 21, 1)
    for k in top_k:
        print(f"==== k = {k} ==== ")
        for dataset in datasets:
            print(f"\tDataset = {dataset.base_table_label} ==== ")
            result_bfs = get_tfd_results(dataset, value_ratio=0.65, top_k=k)
            all_results.extend(result_bfs)
        pd.DataFrame(all_results).to_csv(RESULTS_FOLDER / f"k_{k}_{results_filename}", index=False)
    pd.DataFrame(all_results).to_csv(RESULTS_FOLDER / results_filename, index=False)


def export_neo4j_connections(dataset_label: str = None):
    if dataset_label:
        result = export_dataset_connections(dataset_label)
    else:
        result = export_all_connections()

    pd.DataFrame(result).to_csv(RESULTS_FOLDER / "all_connections-basicdd.csv", index=False)


def transform_arff_to_csv(dataset_label: str, dataset_name: str):
    from scipy.io import arff
    data = arff.loadarff(DATA_FOLDER / dataset_label / dataset_name)
    dataframe = pd.DataFrame(data[0])
    catCols = [col for col in dataframe.columns if dataframe[col].dtype == "O"]
    dataframe[catCols] = dataframe[catCols].apply(lambda x: x.str.decode('utf8'))
    dataframe.to_csv(DATA_FOLDER / dataset_label / f"{dataset_label}_original.csv", index=False)


if __name__ == "__main__":
    # transform_arff_to_csv("superconduct", "superconduct_dataset.arff")
    dataset = filter_datasets(["covertype"])[0]
    get_tfd_results(dataset, value_ratio=0.65, join_all=True, top_k=15)
    # get_arda_results(dataset)
    # get_base_results(dataset)
    # export_neo4j_connections()
