@export()
func buildTags(racEnv string, extra object) object => union(
  {
    rac_env: racEnv
    rac_managed_by: 'bicep'
  },
  extra
)
