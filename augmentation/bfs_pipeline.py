from typing import List, Dict, Set, Tuple

import pandas as pd

from augmentation.trial_error import train_test_cart
from config import JOIN_RESULT_FOLDER
from data_preparation.utils import compute_partial_join_filename, join_and_save, prepare_data_for_ml
from experiments.result_object import Result
from feature_selection.join_path_feature_selection import measure_relevance, measure_conditional_redundancy, \
    measure_joint_mutual_information, measure_redundancy
from graph_processing.neo4j_transactions import get_node_by_id, get_adjacent_nodes, get_relation_properties_node_name
from helpers.util_functions import get_df_with_prefix


class BfsAugmentation:

    def __init__(self, base_table_label: str, target_column: str, value_ratio: float):
        """

        :param base_table_label: The name (label) of the base table to be used for saving data.
        :param target_column: Target column containing the class labels for training.
        :param value_ratio: Pruning threshold. It represents the ration between the number of non-null values in a column and the total number of values.
        """
        self.base_table_label: str = base_table_label
        self.target_column: str = target_column
        self.value_ratio: float = value_ratio
        # Store the accuracy from CART for each join path
        self.ranked_paths: Dict[str, Result] = {}
        # Mapping with the name of the join and the corresponding name of the file containing the join result.
        self.join_name_mapping: Dict[str, str] = {}
        # Set used to track the visited nodes.
        self.discovered: Set[str] = set()
        # Save the selected features of the previous join path (used for conditional redundancy)
        self.partial_join_selected_features: Dict[str, List] = {}

    def bfs_traverse_join_pipeline(self, queue: set, previous_queue=None):
        """
        Recursive function - the pipeline to: 1) traverse the graph given a base node_id, 2) join with the adjacent nodes,
        3) apply feature selection algorithms, and 4) check the algorithm effectiveness by training CART decision tree model.

        :param queue: Queue with one node, which is the starting point of the traversal.
        :param previous_queue: Initially empty or None, the queue is used to store the partial join names between the iterations.
        :return: None
        """

        if len(queue) == 0:
            return

        if previous_queue is None:
            previous_queue = queue.copy()

        # Saves all the paths possible
        # It is used to repopulate the previous_queue after every neighbour node iteration
        initial_queue = previous_queue.copy()

        # Iterate through all the elements of the queue:
        # 1) in the first iteration: queue = base_node_id
        # 2) in all the other iterations: queue = neighbours of the previous node
        while len(queue) > 0:
            # Get the current/base node
            base_node_id = queue.pop()
            self.discovered.add(base_node_id)
            base_node_label = get_node_by_id(base_node_id).get("label")
            print(f"New iteration with base node: {base_node_id}")

            # Determine the neighbours (unvisited)
            neighbours = set(get_adjacent_nodes(base_node_id)) - set(self.discovered)
            if len(neighbours) == 0:
                continue

            # Process every neighbour - join, determine quality, get features
            for node in neighbours:
                if node in self.discovered:
                    continue
                self.discovered.add(node)

                print(f"Adjacent node: {node}")

                # Get all the possible join keys between the base node and the neighbour node
                join_keys = get_relation_properties_node_name(from_id=base_node_id, to_id=node)

                # Read the neighbour node
                right_df, right_label = get_df_with_prefix(node)
                print(f"\tRight table shape: {right_df.shape}")

                # Saves all the paths between base node and the neighbour node generated on every possible join column
                current_queue = set()

                # Iterate through all the previous paths of the join tree
                while len(previous_queue) > 0:
                    # Determine partial join
                    partial_join_name = previous_queue.pop()
                    partial_join, partial_join_name = self.determine_partial_join(partial_join_name, base_node_id)
                    print(f"\tPartial join name: {partial_join_name}")

                    # The current node can only be joined through the base node.
                    # If the base node doesn't exist in the partial join, the join can't be performed
                    if base_node_label not in partial_join_name:
                        print(f"\tBase node {base_node_label} not in partial join {partial_join_name}")
                        continue

                    # Join the same partial join result with the new table on every join column possible
                    for prop in join_keys:
                        join_prop, from_table, to_table = prop
                        if join_prop['from_label'] != from_table and join_prop['weight'] < 1:
                            continue
                        print(f"\t\tJoin properties: {join_prop}")

                        # Step - Sample neighbour data - Transform to 1:1 or M:1
                        sampled_right_df = right_df.groupby(f"{right_label}.{join_prop['to_column']}").sample(n=1,
                                                                                                              random_state=42)

                        # Step - Join
                        joined_df, join_name, join_filename = self.step_join(prop, partial_join_name, partial_join,
                                                                             sampled_right_df)

                        # Step - Data quality
                        data_quality = self.step_data_quality(prop, joined_df)
                        if not data_quality:
                            continue

                        # Step - Feature selection
                        current_selected_features = self.step_feature_selection(joined_df, right_df, partial_join_name,
                                                                                join_name)
                        if current_selected_features is None:
                            continue

                        # Step - Rank path
                        self.step_rank_path(joined_df, current_selected_features, join_name)

                        # Save the join name to be used as the partial join in the next iterations
                        current_queue.add(join_name)
                        self.join_name_mapping[join_name] = join_filename

                # Repopulate with the old paths (initial_queue) and the new paths (current_queue)
                previous_queue.update(initial_queue)
                previous_queue.update(current_queue)

            # When all the neighbours are visited (breadth), go 1 level deeper in the tree traversal
            # Remove the paths from the initial queue when we go 1 level deeper
            self.bfs_traverse_join_pipeline(neighbours, previous_queue - initial_queue)

    def step_join(self, prop: tuple, partial_join_name: str, partial_join: pd.DataFrame,
                  right_df: pd.DataFrame) -> Tuple[pd.DataFrame, str, str]:
        join_prop, from_table, to_table = prop

        # Compute the name of the join
        join_name = compute_partial_join_filename(prop=prop, partial_join_name=partial_join_name)
        print(f"\tJoin name: {join_name}")

        # File naming convention as the filename can be gigantic
        join_filename = f"join_BFS_{self.value_ratio}_{len(self.join_name_mapping) + 1}.csv"

        # Join
        joined_df = join_and_save(left_df=partial_join,
                                  right_df=right_df,
                                  left_column=f"{from_table}.{join_prop['from_column']}",
                                  right_column=f"{to_table}.{join_prop['to_column']}",
                                  label=self.base_table_label,
                                  join_name=join_filename)

        return joined_df, join_name, join_filename

    def step_data_quality(self, prop: tuple, joined_df: pd.DataFrame) -> bool:
        join_prop, from_table, to_table = prop

        # Data Quality check - Prune the joins with high null values ratio
        if joined_df[f"{to_table}.{join_prop['to_column']}"].count() / joined_df.shape[0] < self.value_ratio:
            print(f"\t\tRight column value ration below {self.value_ratio}.\nSKIPPED Join")
            return False

        return True

    def step_feature_selection(self, joined_df: pd.DataFrame, right_df: pd.DataFrame, partial_join_name: str,
                               current_join_name: str) -> List[str] or None:
        print("\t\tFeature selection step ... ")
        current_selected_features = self.partial_join_selected_features[partial_join_name]

        right_features = list(right_df.columns)
        if self.target_column in right_features:
            right_features.remove(self.target_column)

        X, y = prepare_data_for_ml(joined_df, self.target_column)

        # 1. Measure relevance of the new features (right_features) to the target column (y)
        print("\t\tMeasure relevance ... ")
        feature_score_rel, relevant_features = measure_relevance(joined_df, right_features, y)
        if len(relevant_features) == 0:
            print("\t\tNo relevant features. SKIPPED JOIN...")
            return None
        print(f"\t\tRelevant features:\n{relevant_features}")

        # 2. Measure conditional redundancy
        print("\t\tMeasure conditional redundancy ...")
        feature_score_cr, non_cond_red_feat = measure_conditional_redundancy(dataframe=X,
                                                                             selected_features=current_selected_features,
                                                                             new_features=relevant_features,
                                                                             target_column=y)
        print(f"\t\tNon conditional redundant features:\n{non_cond_red_feat}")

        # 3. Measure join mutual information
        print("\t\tMeasure joint mutual information")
        feature_score_jmi, joint_rel_feat = measure_joint_mutual_information(dataframe=X,
                                                                             selected_features=current_selected_features,
                                                                             new_features=relevant_features,
                                                                             target_column=y)
        print(f"\t\tJoint relevant features:\n{joint_rel_feat}")
        if len(non_cond_red_feat) == 0:
            if len(joint_rel_feat) == 0:
                print("\t\tAll relevant features are redundant. SKIPPED JOIN...")
                return None
            else:
                selected_features = set(joint_rel_feat)
        else:
            selected_features = set(non_cond_red_feat).intersection(set(joint_rel_feat))

        # 4. Measure redundancy in the dataset
        print("\t\tMeasure redundancy in the dataset ... ")
        feature_score_redundancy, non_red_feat = measure_redundancy(dataframe=X,
                                                                    feature_group=list(selected_features),
                                                                    target_column=y)
        if len(non_red_feat) == 0:
            print("\t\tAll relevant features are redundant. SKIPPED JOIN...")
            return None
        print(f"\t\tNon redundant features:\n{non_red_feat}")

        selected_features = non_red_feat.copy()
        selected_features.extend(current_selected_features)
        self.partial_join_selected_features[current_join_name] = selected_features

        return selected_features

    def step_rank_path(self, joined_df: pd.DataFrame, features: List[str], join_name: str):
        columns = features.copy()
        columns.append(self.target_column)
        result = train_test_cart(dataframe=joined_df[columns], target_column=self.target_column)
        self.ranked_paths[join_name] = result

    def determine_partial_join(self, partial_join_name: str, base_node_id: str) -> Tuple[pd.DataFrame, str]:
        if partial_join_name == base_node_id:
            partial_join, partial_join_name = get_df_with_prefix(base_node_id, self.target_column)
            self.partial_join_selected_features[partial_join_name] = self.get_relevant_features(partial_join)
        else:
            partial_join = pd.read_csv(
                JOIN_RESULT_FOLDER / self.base_table_label / self.join_name_mapping[partial_join_name], header=0,
                engine="python", encoding="utf8", quotechar='"', escapechar='\\')
        return partial_join, partial_join_name

    def get_relevant_features(self, partial_join: pd.DataFrame) -> List[str]:
        X, y = prepare_data_for_ml(partial_join, self.target_column)
        feature_score, selected_features = measure_relevance(partial_join, X.columns, y)
        return selected_features
