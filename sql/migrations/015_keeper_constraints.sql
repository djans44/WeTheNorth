alter table keeper_contracts
    add constraint keeper_contracts_penalty_round_range
    check (penalty_round is null or penalty_round between 1 and 13);

comment on column keeper_contracts.penalty_round is
    'Defence must be drafted at this round: least(current cost + 3, 13).';

comment on index keeper_selections_cost_key is
    'Two keepers on one team cannot share a cost round. A collision is resolved
     by moving one to a more expensive round, which is impossible at round 1,
     so two round-1 keepers are not permitted.';
