import json

import pandas as pd

import data_preparation.utils
from augmentation.trial_error import dfs_traverse_join_pipeline, bfs_traverse_join_pipeline
from config import RESULTS_FOLDER
from graph_processing.traverse_graph import dfs_traversal

# node_id = "/Users/andra/Developer/auto-data-augmentation/data/ARDA/school/base.csv"
# target = "class"
# base_table_features = ["DBN", "School Name", "School Type", "Total Parent Response Rate (%)",
#                        "Total Teacher Response Rate (%)", "Total Student Response Rate (%)"]

node_id = "/Users/andra/Developer/auto-data-augmentation/data/cs/target_churn.csv"
target = "target_churn"
base_table_features = ["ACC_KEY", "date_horizon"]

# node_id = "/Users/andra/Developer/auto-data-augmentation/data/air/temp.csv"
# target = "Temperature"
# base_table_features = ["Day", "f0_", "f1_", "f2_", "f3_", "f4_", "f5_", "f6_"]


def test_arda():
    from arda.arda import select_arda_features_budget_join
    import algorithms

    sample_size = 1000

    dataframe, dataframe_label, selected_features, join_time, _ = select_arda_features_budget_join(node_id,
                                                                                                   target,
                                                                                                   base_table_features,
                                                                                                   sample_size=sample_size)
    print(f"X shape: {dataframe.shape}\nSelected features:\n\t{selected_features}")

    features = [f"{dataframe_label}.{feat}" for feat in base_table_features]
    features.extend(selected_features)
    features.append(target)
    X, y = data_preparation.utils.prepare_data_for_ml(dataframe[features], target)
    acc_decision_tree, params, feature_importance, train_time, _ = algorithms.CART().train(X, y, regression=True)
    features_scores = dict(zip(feature_importance, X.columns))
    print(f"\tAccuracy: {acc_decision_tree}\n\tFeature scores: \n{features_scores}\n\tTrain time: {train_time}")


def test_dfs_pipeline():
    visited = []
    join_path_tree = {}
    join_name_mapping = {}
    train_results = []
    value_ratio = 0.35
    dfs_traversal(base_node_id=node_id, discovered=visited, join_tree=join_path_tree)

    with open('join_tree_dfs.json', 'w') as f:
        json.dump(join_path_tree, f)

    all_paths = dfs_traverse_join_pipeline(base_node_id=node_id, target_column=target, join_tree=join_path_tree,
                                           train_results=train_results, join_name_mapping=join_name_mapping,
                                           value_ratio=value_ratio)
    print(f"FINISHED DFS")
    pd.Series(list(all_paths), name="filename").to_csv(
        RESULTS_FOLDER / f"paths_dfs_{value_ratio * 100}.csv", index=False)
    pd.DataFrame(train_results).to_csv(
        RESULTS_FOLDER / f"results_dfs_pruning_{value_ratio * 100}.csv", index=False)
    pd.DataFrame.from_dict(join_name_mapping, orient='index', columns=["join_name"]).to_csv(
        RESULTS_FOLDER / f'join_mapping_dfs_pruning_{value_ratio * 100.}.csv')


def test_base_accuracy():
    import algorithms

    dataframe = pd.read_csv(node_id, header=0, engine="python", encoding="utf8", quotechar='"', escapechar='\\')
    X, y = data_preparation.utils.prepare_data_for_ml(dataframe, target)
    acc_decision_tree, params, feature_importance, train_time, _ = algorithms.CART().train(X, y)
    features_scores = dict(zip(feature_importance, X.columns))
    print(f"\tAccuracy: {acc_decision_tree}\n\tFeature scores: \n{features_scores}\n\tTrain time: {train_time}")


def test_bfs_pipeline():
    results = []
    all_paths = {}
    join_name_mapping = {}
    queue = {node_id}
    value_ratio = 0.35

    bfs_traverse_join_pipeline(queue=queue, target_column=target, train_results=results,
                               join_name_mapping=join_name_mapping, value_ratio=value_ratio, all_paths=all_paths)
    print("FINISHED BFS")

    # Save results
    pd.DataFrame(results).to_csv(
        RESULTS_FOLDER / f"results_bfs_{value_ratio * 100}.csv", index=False)
    pd.DataFrame.from_dict(join_name_mapping, orient='index', columns=["join_name"]).to_csv(
        RESULTS_FOLDER / f'join_mapping_bfs_{value_ratio * 100}.csv')

    with open(RESULTS_FOLDER / f"all_paths_{value_ratio * 100}.json", "w") as f:
        json.dump(all_paths, f)


test_bfs_pipeline()
# test_dfs_pipeline()
# test_base_accuracy()
# test_arda()
