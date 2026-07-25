"""The back office owns no models of its own.

It is a shell: it provides the shared chrome (base template, dashboard, nav),
the access gate, and a render helper. Each authoring *module* lives in the app
that owns the data it edits (e.g. the quiz editor in ``toto.quizzes``).
"""
