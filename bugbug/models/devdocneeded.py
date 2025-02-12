# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel


class DevDocNeededModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization, commit_data=True)

        self.cross_validation_enabled = False

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.HasRegressionRange(),
            bug_features.Severity(),
            bug_features.Keywords({"dev-doc-needed", "dev-doc-complete"}),
            bug_features.IsCoverityIssue(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            bug_features.Whiteboard(),
            bug_features.Patches(),
            bug_features.Landings(),
            bug_features.Product(),
            bug_features.Component(),
            bug_features.CommitAdded(),
            bug_features.CommitDeleted(),
            bug_features.CommitTypes(),
        ]

        cleanup_functions = [
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(
                        feature_extractors,
                        cleanup_functions,
                        rollback=True,
                        rollback_when=self.rollback,
                        commit_data=True,
                    ),
                ),
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(), "title"),
                            ("comments", self.text_vectorizer(), "comments"),
                        ]
                    ),
                ),
            ]
        )

        self.hyperparameter = {"n_jobs": utils.get_physical_cpu_count()}
        self.clf = xgboost.XGBClassifier(**self.hyperparameter)

    def rollback(self, change):
        return change["field_name"] == "keywords" and any(
            keyword in change["added"]
            for keyword in ["dev-doc-needed", "dev-doc-complete"]
        )

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs():
            bug_id = int(bug_data["id"])

            found_dev_doc = False
            if any(
                keyword in bug_data["keywords"]
                for keyword in ["dev-doc-needed", "dev-doc-complete"]
            ):
                classes[bug_id] = 1
                found_dev_doc = True

            if not found_dev_doc:
                for entry in bug_data["history"]:
                    for change in entry["changes"]:
                        # Bugs that get dev-doc-needed removed from them at some point after it's been added (this suggests a false positive among human-analyzed bugs)
                        if (
                            change["field_name"] == "keywords"
                            and "dev-doc-needed" in change["removed"]
                            and "dev-doc-complete" not in change["added"]
                        ):
                            classes[bug_id] = 0
                        # Bugs that go from dev-doc-needed to dev-doc-complete are guaranteed to be good
                        # Bugs that go from not having dev-doc-needed to having dev-doc-complete are bugs
                        # that were missed by previous scans through content but someone realized it
                        # should have been flagged and updated the docs, found the docs already updated.
                        elif change["field_name"] == "keywords" and any(
                            keyword in change["added"]
                            for keyword in ["dev-doc-needed", "dev-doc-complete"]
                        ):
                            classes[bug_id] = 1

            if bug_id not in classes:
                classes[bug_id] = 0

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names_out()
