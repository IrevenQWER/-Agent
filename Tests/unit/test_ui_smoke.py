from futureedu_insight.ui.gradio_app import build_app


def test_gradio_teacher_workbench_builds() -> None:
    app = build_app()

    assert app is not None
