from __future__ import annotations


def test_condition_groups_match_by_or_and_numeric_and_selection():
    import app.services.approval as approval_service

    context = {
        "applicant": {"id": 7, "department_id": 20, "company_id": 3, "roles": ["employee"]},
        "duration": 1,
        "leave_type": "病假",
    }
    branches = [
        {
            "label": "条件 1",
            "condition_group_combinator": "or",
            "condition_groups": [
                {
                    "condition_combinator": "and",
                    "conditions": [
                        {"field": "applicant.scope", "operator": "in", "value": [{"type": "department", "id": 99}]},
                        {"field": "duration", "operator": "lte", "value": 1},
                    ],
                },
                {
                    "condition_combinator": "and",
                    "conditions": [
                        {"field": "duration", "operator": "lte", "value": 1},
                        {"field": "leave_type", "operator": "in", "value": ["事假", "病假"]},
                    ],
                },
            ],
            "nodes": [{"node_type": "approval", "approver_type": "department_head"}],
        },
        {"label": "默认分支", "is_default_branch": True, "nodes": [{"node_type": "approval", "approver_type": "hr"}]},
    ]

    selected = approval_service._select_matching_branch(branches, context)

    assert selected["label"] == "条件 1"


def test_condition_branch_falls_back_to_default_when_groups_do_not_match():
    import app.services.approval as approval_service

    context = {
        "applicant": {"id": 7, "department_id": 20, "company_id": 3, "roles": ["employee"]},
        "duration": 3,
        "leave_type": "年假",
    }
    branches = [
        {
            "label": "条件 1",
            "condition_group_combinator": "or",
            "condition_groups": [
                {
                    "condition_combinator": "and",
                    "conditions": [
                        {"field": "applicant.scope", "operator": "in", "value": [{"type": "role", "value": "manager"}]},
                        {"field": "duration", "operator": "between", "value": [0, 1]},
                    ],
                }
            ],
            "nodes": [{"node_type": "approval", "approver_type": "department_head"}],
        },
        {"label": "默认分支", "is_default_branch": True, "nodes": [{"node_type": "approval", "approver_type": "hr"}]},
    ]

    selected = approval_service._select_matching_branch(branches, context)

    assert selected["label"] == "默认分支"


def test_duration_condition_uses_leave_duration_alias():
    import app.services.approval as approval_service

    context = {
        "applicant": {"id": 7, "department_id": 20, "company_id": 3, "roles": ["employee"]},
        "form": {"leave_duration": {"value": 2}, "leave_type": "年假"},
        "leave_duration": {"value": 2},
        "leave_type": "年假",
    }
    branches = [
        {
            "label": "两天内",
            "condition_group_combinator": "or",
            "condition_groups": [
                {
                    "condition_combinator": "and",
                    "conditions": [
                        {"field": "duration", "operator": "lte", "value": 2},
                        {"field": "leave_type", "operator": "in", "value": ["年假"]},
                    ],
                }
            ],
            "nodes": [{"node_type": "approval", "approver_type": "direct_manager"}],
        },
        {"label": "默认分支", "is_default_branch": True, "nodes": [{"node_type": "approval", "approver_type": "hr"}]},
    ]

    selected = approval_service._select_matching_branch(branches, context)

    assert selected["label"] == "两天内"


def test_condition_branch_merges_back_to_main_flow_after_selected_branch():
    import app.services.approval as approval_service

    context = {
        "applicant": {"id": 7, "department_id": 20, "company_id": 3, "roles": ["employee"]},
        "duration": 1,
        "leave_type": "病假",
    }
    configured_nodes = [
        {
            "node_order": 1,
            "node_type": "approval",
            "approver_type": "direct_manager",
            "name": "发起后直属上级",
        },
        {
            "node_order": 2,
            "node_type": "condition_branch",
            "condition_rules": {
                "branches": [
                    {
                        "label": "条件1",
                        "condition_group_combinator": "or",
                        "condition_groups": [
                            {
                                "condition_combinator": "and",
                                "conditions": [
                                    {"field": "duration", "operator": "lte", "value": 1},
                                    {"field": "leave_type", "operator": "in", "value": ["病假"]},
                                ],
                            }
                        ],
                        "nodes": [
                            {
                                "node_order": 1,
                                "node_type": "approval",
                                "approver_type": "department_head",
                                "name": "条件分支主管",
                            }
                        ],
                    },
                    {
                        "label": "默认条件",
                        "is_default_branch": True,
                        "nodes": [
                            {
                                "node_order": 1,
                                "node_type": "approval",
                                "approver_type": "hr",
                                "name": "默认 HR",
                            }
                        ],
                    },
                ]
            },
        },
        {
            "node_order": 3,
            "node_type": "notify",
            "approver_type": "applicant",
            "name": "汇入后的抄送",
        },
    ]

    effective_nodes = approval_service._expand_effective_nodes(configured_nodes, context)

    assert [node["node_type"] for node in effective_nodes] == ["approval", "approval", "notify"]
    assert [node["approver_type"] for node in effective_nodes] == [
        "direct_manager",
        "department_head",
        "applicant",
    ]
    assert [node["node_order"] for node in effective_nodes] == [1, 2, 3]


def test_empty_selected_branch_passes_through_to_following_canvas_nodes():
    import app.services.approval as approval_service

    context = {
        "applicant": {"id": 7, "department_id": 20, "company_id": 3, "roles": ["employee"]},
        "duration": 1,
    }
    configured_nodes = [
        {
            "node_order": 1,
            "node_type": "condition_branch",
            "condition_rules": {
                "branches": [
                    {
                        "label": "短时长",
                        "condition_groups": [
                            {
                                "condition_combinator": "and",
                                "conditions": [{"field": "duration", "operator": "lte", "value": 1}],
                            }
                        ],
                        "nodes": [],
                    },
                    {
                        "label": "默认条件",
                        "is_default_branch": True,
                        "nodes": [{"node_type": "approval", "approver_type": "hr"}],
                    },
                ]
            },
        },
        {
            "node_order": 2,
            "node_type": "approval",
            "approver_type": "direct_manager",
            "name": "汇入后的直属上级",
        },
    ]

    effective_nodes = approval_service._expand_effective_nodes(configured_nodes, context)

    assert [node["node_type"] for node in effective_nodes] == ["approval"]
    assert [node["approver_type"] for node in effective_nodes] == ["direct_manager"]
    assert [node["node_order"] for node in effective_nodes] == [1]


def test_nested_condition_branches_also_merge_to_following_canvas_nodes():
    import app.services.approval as approval_service

    context = {
        "applicant": {"id": 7, "department_id": 20, "company_id": 3, "roles": ["employee"]},
        "duration": 5,
        "leave_type": "年假",
    }
    nested_branch = {
        "node_order": 2,
        "node_type": "condition_branch",
        "condition_rules": {
            "branches": [
                {
                    "label": "长假",
                    "condition_groups": [
                        {
                            "condition_combinator": "and",
                            "conditions": [
                                {"field": "duration", "operator": "gte", "value": 3},
                            ],
                        }
                    ],
                    "nodes": [
                        {
                            "node_order": 1,
                            "node_type": "approval",
                            "approver_type": "hr",
                            "name": "嵌套分支 HR",
                        }
                    ],
                },
                {
                    "label": "默认条件",
                    "is_default_branch": True,
                    "nodes": [
                        {
                            "node_order": 1,
                            "node_type": "approval",
                            "approver_type": "direct_manager",
                            "name": "嵌套默认主管",
                        }
                    ],
                },
            ]
        },
    }
    configured_nodes = [
        {
            "node_order": 1,
            "node_type": "condition_branch",
            "condition_rules": {
                "branches": [
                    {
                        "label": "年假",
                        "condition_groups": [
                            {
                                "condition_combinator": "and",
                                "conditions": [
                                    {"field": "leave_type", "operator": "in", "value": ["年假"]},
                                ],
                            }
                        ],
                        "nodes": [
                            {
                                "node_order": 1,
                                "node_type": "approval",
                                "approver_type": "department_head",
                                "name": "外层分支主管",
                            },
                            nested_branch,
                            {
                                "node_order": 3,
                                "node_type": "notify",
                                "approver_type": "applicant",
                                "name": "外层分支内汇入抄送",
                            },
                        ],
                    },
                    {
                        "label": "默认条件",
                        "is_default_branch": True,
                        "nodes": [
                            {
                                "node_order": 1,
                                "node_type": "approval",
                                "approver_type": "direct_manager",
                                "name": "外层默认主管",
                            }
                        ],
                    },
                ]
            },
        },
        {
            "node_order": 2,
            "node_type": "approval",
            "approver_type": "finance",
            "name": "最终汇入财务",
        },
    ]

    effective_nodes = approval_service._expand_effective_nodes(configured_nodes, context)

    assert [node["node_type"] for node in effective_nodes] == ["approval", "approval", "notify", "approval"]
    assert [node["approver_type"] for node in effective_nodes] == [
        "department_head",
        "hr",
        "applicant",
        "finance",
    ]
    assert [node["node_order"] for node in effective_nodes] == [1, 2, 3, 4]
