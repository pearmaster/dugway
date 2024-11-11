from stevedore import extension

if __name__ == '__main__':
    mgr = extension.ExtensionManager(
        namespace='dugwayservice',
        invoke_on_load=False,
    )

    print(mgr.entry_points_names())


    for x in mgr:
        print(f"{x=} {x.module_name=} {x.entry_point_target=} {x.attr=}")
