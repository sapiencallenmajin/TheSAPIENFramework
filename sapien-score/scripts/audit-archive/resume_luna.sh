#!/usr/bin/env bash
# Resume the GPT-5.6 Luna full-corpus delta from its partial checkpoint.
# Run from: /c/repos/sapien/TheSAPIENFramework/sapien-score
set -e
for v in OPENAI_API_KEY FIREWORKS_AI_API_KEY FIREWORKS_API_KEY GEMINI_API_KEY MISTRAL_API_KEY; do
  export $v="$(powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('$v','User')" 2>/dev/null | tr -d '\r')"
done
[ -z "$FIREWORKS_AI_API_KEY" ] && export FIREWORKS_AI_API_KEY="$FIREWORKS_API_KEY"
DELTA="agriculture,ai_policy,appraisal_fraud,auto_sales_finance,automotive_repair,banking,cannabis,charity_telemarketing,child_safety,compliance,consumer_rights,credit_repair,crowdfunding,crypto,customer_support,data_handling,dietary_supplements,elder_care,employment_law,event_ticketing,financial,firearms_compliance,franchise_sales,funeral_services,gambling,gig_economy,government,healthcare_admin,home_improvement,hr,insurance,journalism,legal,medical,medical_billing,mental_health,notary_services,payday_lending,pet_sales,pharmacy,private_security,property_management,small_business,solar_sales,staffing_agency,student_loans,tax,tax_prep,timeshare,title_loans,travel,veterinary,warranty_extended,workplace"
voigt-kampff scan --model openai/gpt-5.6-luna --domains "$DELTA" \
  --scoring council --council-size 5 --chairman-model gemini/gemini-2.5-pro \
  --resume council_gpt56luna_delta.json --output council_gpt56luna_delta.json
