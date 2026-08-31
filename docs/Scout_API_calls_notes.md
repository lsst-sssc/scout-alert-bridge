# Investigating options for reducing Scout API calls on target retirement

## Target Retirement

### Previous version

In `ingest_scout.py` there was a `_sweep(self, seen_ids)` method which took in `seen_ids` which was assembled earlier in the `handle()` by iterating over the results from the Scout API.

Then in `_sweep()` itself, it was a simple case of doing a `ScoutDetail.update(active=False)` on all the currently active targets that **weren't** in `seen_ids`

### Issues arising from the review

Joey's review comment:
> This whole method is problematic given the current command design. With an arbitrary scout query that limits what of the full DB is returned, you have no way of knowing if a target just doesn't make your current cut, or if has truly been removed.
> The only way to properly and safely do this is to search for the specific target itself.



### New Version

New code in `updatescout.py` has a `_reconcile()` which fetches all the active Scout targets (through `ScoutDetail.objects.filter(active=True`) and then build target-specific API queries for every target (which isn't throttled)

## Designation Updating

### Previous version

After fetching a list of last 100 Previous NEOCP objects and returning a `dict` of `"<NEOCP designation>" : "<final (IAU) designation>"`, iterates over those targets in the map which are Scout targets and creates `TargetName` objects with the IAU designation

### Review comment and new version

Joey thought that this was backward and so the new code sets `target.name` to the IAU designation and then does a `TargetName.objects.get_or_create()` for the old name.

XXX Not sure how this handles multiple NEOCP objects (from different surveys) getting the same final IAU designation.
