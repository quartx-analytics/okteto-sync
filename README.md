# Okteto Deployment Sync

Synchronise GitHub deployments with Okteto deployments. Removes stale GitHub & Okteto deployments.

This was built to keep the Github deployment environments clean of inactive Okteto environments that no
longer exists. Or to delete Github and Okteto environments when a GitHub branch has been deleted.

# Inputs

## `github-regex`

Regex used to select only the Okteto deployments. Default `"^Preview .+$"`.

## `okteto-regex`

**Required** Regex used to extract the branch name from the Okteto deployment name. Example `"^(.+)-quartx$"`

## `github-token`

The token used to authenticate with GitHub. Defaults to `github.token`.

## `dry-run`

Run the script without making any changes. Default `"false"`.

## Example usage
```yaml
on:
  workflow_dispatch:
    inputs:
      dry-run:
        description: Run the script without making any changes.
        required: false
        type: string
  schedule:
    - cron: "0 23 */2 * *"

jobs:
  registry:
    runs-on: ubuntu-latest
    steps:
      - name: Login to Okteto
        uses: okteto/context@latest
        with:
          token: ${{ secrets.OKTETO_TOKEN }}
      
      - name: Run Okteto Sync
        uses: quartx-analytics/okteto-sync@main
        with:
          github-regex: "^Preview .+$"
          okteto-regex: "^(.+)-quartx$"
          dry-run: ${{ inputs.dry-run || 'false' }}
```

# License
The scripts and documentation in this project are released under the [Apache License](LICENSE)
