"""Reader-facing matrix vocabulary; source predicates and values stay unchanged."""
TITLES = dict(line.split('|', 1) for line in '''funding|Probe funding
ret_missing_transfer|Transfer returns no value
ret_missing_approve|Approval returns no value
ret_missing_transferFrom|Delegated transfer returns no value
false_on_failure|Returns false on failure
recipient_not_credited|Recipient is not credited
fresh_address_nonzero_balance|Unused address has a balance
transfer_reverts_fresh_contract|Fresh contract transfer reverts
permissioned_transfer|Transfer needs permission
transfer_reverts_paused|Transfer reverts while paused
transfer_reverts_market_or_router|Market or router restricts transfer
fee_on_transfer_observed|Transfer fee observed
transfer_rounding|Transfer amount rounds
fee_switch_capability|Fee setting available
fee_param_nonzero|Fee setting is nonzero
approve_race_guard|Approval reset required
approve_zero_addr_reverts|Approval to zero address reverts
approve_zero_value_reverts|Zero approval reverts
approve_max_capped|Maximum approval capped
allowance_helpers|Allowance helpers available
tf_owner_no_allowance|Own transfer needs no allowance
allowance_max_decrements|Maximum allowance decreases
tf_zero_reverts|Zero delegated transfer reverts
transfer_zero_reverts|Zero transfer reverts
transfer_zero_returns_false|Zero transfer returns false
to_zero_reverts|Transfer to zero address reverts
to_zero_burns|Transfer to zero burns supply
self_transfer_anomaly|Self-transfer behaves unusually
self_transfer_changes_balance|Self-transfer changes balance
to_token_contract_reverts|Transfer to token address reverts
to_dead_reverts|Transfer to burn address reverts
uint96_limit|Balance field at most 96 bits
balance_field_le128|Balance field is 97–128 bits
max_uint_transfer_succeeds|Maximum amount transfers all
full_balance_anomaly|Full-balance transfer unusual
max_tx_limit|Large transfer restricted
decimals|Decimal places
decimals_low|At most five decimals
decimals_high|More than eighteen decimals
metadata_nonstring|Name or symbol is not a string
name_injection|Name contains markup or controls
permit_any|Signature approval available
permit_dai_style|DAI-style signature approval
pause_capability|Pause capability present
trading_gate_capability|Trading switch present
paused_now|Currently paused
pause_w1_any|Pause or trading switch present
blocklist_capability|Blocklist capability present
blocklist_view_answers|Blocklist view responds
botlist_capability|Anti-bot list present
blocklist_any|Blocklist evidence present
sanction_block_ronin|Tested Ronin address rejected
sanction_block_tornado|Tested Tornado router rejected
share_accounting_ratio|Balance uses share accounting
share_accounting_capability|Share-accounting capability present
share_accessors|Share accessors available
balance_drift_7d|Balance drifts over a week
rebasing_w1_any|Rebasing or shares detected
hook_callbacks_observed|Transfer hook observed
erc777_static|ERC-777 indicators present
callback_into_recipient|Recipient callback observed
contract_recipient_reverts|Contract recipient rejected
gas_transfer|Transfer gas exceeds threshold
anti_bot_controls|Anti-bot controls present
same_block_cooldown|Same-block transfer restricted
eoa_vs_contract_differs|Wallet and contract differ
forwarding_proxy|Calls forwarded to another address
external_balance_storage|Balances stored elsewhere
native_currency_repr|Represents native currency
flash_mint_capability|Flash mint capability present
is_proxy|Proxy or clone detected
upgradeable|Upgrade authority exists
upgrade_authority_class|Upgrade authority type
upgrade_single_eoa|Single-account upgrade path
upgrade_no_timelock|Upgrade path has no timelock
owner_path_class|Owner or admin type
owner_single_eoa|Single-account owner path
owner_renounced|Owner path renounced
controlled|Privileged controller exists
mint_capability|Mint selector present
burn_from_others_capability|Burn-from-others selector present
rescue_capability|Token rescue selector present
blacklist_hint|Blocklist selector hint
pauser_role_eoa|Account holds pause role
blacklister_role_eoa|Account holds blocklist role
minter_role_eoa|Account holds mint role'''.splitlines())

FAMILIES = {
    'meta': 'Harness metadata', 'transfer_return': 'Transfer results',
    'approve': 'Approvals', 'transferFrom': 'Delegated transfers',
    'permissioned': 'Permissioned transfers', 'fee_on_transfer': 'Fee on transfer',
    'zero_self': 'Zero and self-transfers', 'large_amounts': 'Large amounts',
    'metadata': 'Token metadata', 'pause': 'Pausable tokens',
    'blocklist': 'Tokens with blocklists', 'rebasing': 'Balance modifications outside transfers (rebasing)',
    'hooks': 'Reentrant calls (hooks)', 'control': 'Control and upgrade paths',
    'gas': 'Transfer gas use', 'other': 'Token mechanics',
}


def decorate_columns(columns, dxo_rows):
    categories = {}
    for row in dxo_rows:
        if row.get('dim') == 'all' and row.get('group') == 'all':
            for feature in row['features'].split(';'):
                categories.setdefault(feature, []).append(row['d_xo_section'])
    missing = {c['id'] for c in columns} - TITLES.keys()
    if missing:
        raise ValueError(f'Add reader-facing titles for new features: {sorted(missing)}')
    return [dict(c, title=TITLES[c['id']], family_title=FAMILIES.get(c.get('family'), 'Extended tests'),
                 dxo=categories.get(c['id'], [])) for c in columns]
