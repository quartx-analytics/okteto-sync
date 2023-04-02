# Okteto Deployment Sync

Synchronise GitHub deployments with Okteto deployments.

This action will keep the GitHub deployed environments clean of inactive or stale Okteto preview environments.
When a branch no longer exists for a deployment, both that deployment and Okteto preview environment will be deleted.
If an Okteto preview environment has been deleted then the related GitHub deployment will be deleted too. The only
requirement is that the deployments on GitHub contain a url pointing to the Okteto preview environment.

# Inputs

## `dry-run`

Run the script without making any changes. Default `"false"`.

## `github-token`

The token used to authenticate with GitHub. Defaults to `github.token`.

## `okteto-domain`

The domain where Okteto is hosted. Defaults to `cloud.okteto.net`."

## `ignore-deployments`

List of long-lived deployments to ignore. This is not required, but is recommended.
As GitHub keeps track of the full deployment history for an environment. This script can
really slow down when scanning long-lived deployments.


## Example usage
```yaml
on:
  workflow_dispatch:
    inputs:
      dry-run:
        description: Run the script without making any changes.
        default: false
        type: boolean
  schedule:
    - cron: "0 23 */2 * *"

jobs:
  okteto-sync:
    runs-on: ubuntu-latest
    steps:
      - name: Login to Okteto
        uses: okteto/context@latest
        with:
          token: ${{ secrets.OKTETO_TOKEN }}
      
      - name: Run Okteto Sync
        uses: quartx-analytics/okteto-sync@v2
        with:
          dry-run: ${{ inputs.dry-run || 'false' }}
          ignore-deployments: Staging, Production
```

# License
The scripts and documentation in this project are released under the [Apache License](LICENSE)
