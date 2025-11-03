import wkls


def test_falkland_island():
    assert wkls.fk.wkt().startswith(
        "MULTIPOLYGON (((-59.1909483 -52.9191095, -59.1765549 -52.8936952, -59.1879201 -52.8916291,"
    )
    assert len(wkls.fk.cities()) == 25
    assert wkls.fk.stoneyridge.wkt().startswith(
        "POLYGON ((-60.4887893 -52.014296, -60.4914225 -52.0133724, -60.4941385 -52.0139674, -60.491533 -52.0158203,"
    )


def test_dependencies_function():
    assert len(wkls.dependencies()) == 53
