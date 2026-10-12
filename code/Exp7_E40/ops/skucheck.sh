#!/usr/bin/env bash
echo "aks list: $(az aks list -g mcb-experiments --query '[].name' -o tsv)"
echo "MC_ group exists: $(az group exists -n MC_mcb-experiments_e40-aks_southeastasia)"
for r in southeastasia eastasia; do
  for s in Standard_B2s Standard_B2als_v2 Standard_B2as_v2 Standard_B2s_v2 Standard_D2s_v3 Standard_D2as_v5 Standard_D2ads_v5 Standard_A2_v2 Standard_DC2ds_v3 Standard_F2s_v2; do
    rc=$(az vm list-skus -l $r --size $s --query '[0].restrictions[].reasonCode' -o tsv 2>/dev/null | tr '\n' ' ')
    echo "$r $s restrictions=[${rc}]"
  done
done
az vm list-usage -l southeastasia -o tsv --query "[?currentValue>=\`0\`].[name.value,currentValue,limit]" | grep -iE 'cores|BS|DCdsv3|standardDSv3|DASv5|FSv2' | head -20
