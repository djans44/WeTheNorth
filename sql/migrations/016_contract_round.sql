alter table keeper_contracts
    add column contract_round smallint null check (contract_round between 1 and 13);

alter table keeper_contracts drop constraint keeper_contracts_adp_required;

alter table keeper_contracts
    add constraint keeper_contracts_round_required
    check (contract_years = 1 or contract_round is not null);

comment on column keeper_contracts.contract_round is
    'Cost in contract years two and three: least(original_round, adp_round + 2),
     locked at signing. Adjusted at signing if it collides with another keeper.';
comment on column keeper_contracts.adp_round is
    'ADP at signing, where known. Not recoverable when contract_round equals original_round.';
