# B4-PE freeze candidate v3

## Authorization boundary

This remains a freeze candidate. It authorizes only the B4-PE Pilot execution
path:

- Pilot: authorized;
- Negative Control: not authorized;
- Formal: not authorized;
- I5D statistics: authorized subject to each mode's independent fail-closed
  gate;
- paper results: not authorized.

Pilot authorization permits the 2400-case Pilot to be started after independent
authorization review. It does not mean that the Pilot neutral gate has passed,
and Pilot output must not be described as a formal paper result.

The scientific code identity remains
`87fae1924591fa2c0cabd292c03df043d5eea9fd`. The pre-Pilot candidate v3 SHA-256
was `708e3b90e294e560604e34e7052a3314a4fd7580b86295ebcd7e0182fada21cd`;
the Pilot-authorized candidate JSON SHA-256 is
`c30c74c971cb82f01d243f733e5276b04ff4e862d317fe501a235c55070712cf`.
This governance transition changes no algorithm, parameter, protocol, task
generation, seed/identity rule, case count, or statistics contract. Candidate
v2 and its supersedes chain remain byte-identical.

`final_code_commit`, `final_git_tag`, `formal_runtime_binary_path`, and
`formal_runtime_binary_sha256` remain null. No tag exists for this candidate.
Independent review and RTA integration are not closed. The RTA condition does
not block Pilot, but it continues to block Formal execution and the final paper
freeze.

## Pilot Release runtime seal

The logical runtime executable is `pilot-runtime/bin/rtsim`:

- SHA-256:
  `96004d1aec42cac73bea72d4fe0d5c2a5e814453bfeeb16d09026c4ff8746f7d`;
- size: 183664 bytes;
- artifact: ELF64 x86-64 PIE (`ET_DYN`), dynamically linked, not stripped;
- loader environment: `LD_LIBRARY_PATH=pilot-runtime/lib`.

The sorted project-controlled dependency closure is:

| Role | Logical path | SHA-256 | Bytes |
| --- | --- | --- | ---: |
| command-line parser | `pilot-runtime/lib/libcmdarg.so.0` | `02aa859ea7eee6a5b3c3c6c32826656349ee629f19d5a86c245acfb44186c5fd` | 136960 |
| simulation kernel | `pilot-runtime/lib/libmetasim.so.3` | `20734b7ffff7db8352593aa1c89f20716dbcec8462e638591e9855d20525e324` | 237408 |
| real-time simulation | `pilot-runtime/lib/librtsim.so.3` | `f566e702435da6070059ff5ec1b47b7b8063e5081db1eb2351de57bc3f6245de` | 5151792 |

The dependency manifest is the compact UTF-8 JSON serialization of that list,
with sorted object keys, dependency order by logical path, and one trailing LF.
Its SHA-256 is
`7e76172d7a64ba02ba9b29cc2c415a573404de7675dd9c89cb6d2a64d6d262e5`.

The runtime was built before this governance edit through
`deployment/autodl/build_simulator.sh` at SHA-256
`36d4a999e490bd4527655ded0e009e6d57a80c4c4e6cc0c110278afe2b280334`.
The fully resolved CMake configure used the audited checkout
`/home/devcontainers/PARTsim-b4-pe-pilot-authorization`, the fresh external
build root `/tmp/b4_pe_pilot_runtime_87fae192/build`,
`-DCMAKE_BUILD_TYPE=Release`, and `-DBUILD_TESTING=OFF`. The build used
`cmake --build /tmp/b4_pe_pilot_runtime_87fae192/build --parallel 1`,
Unix Makefiles, CMake 3.16.3, GNU C++ 9.4.0, Python 3.8.10, and NumPy 1.17.4.
The candidate JSON records the complete argv and deterministic build
environment.

## Python, protocol, and contract identities

The Pilot runtime seal also binds:

- task generator:
  `global_task_generator.py`,
  `25147e8073e55885a035160cbec4fe1d094dfd423a476731790e4cb8bb53bb8e`;
- system template:
  `v9_3_b4_priority_energy_system_template.yml`,
  `a64181bf9fda8155c5b0b8b0451a160d6c44c2c8fae188a974640a4d2b243510`;
- manifest protocol v3:
  `c51e774e74ad3ce9bb4d39bacfccb5a7c64e71750c6a0f12432c4ab70070603f`;
- execution protocol v3:
  `b76a44ac48c1721e4a0b2042a53d787c22b78a0ec017ea171d92534fd1d107ec`;
- integration-smoke protocol v3:
  `5517b35afab8c65ac1ab045b047f8032169abf2c3efa7c60a21de5d4d311d9fb`;
- observability contract v2:
  `4e982f5a58a26507c9ab1b1b8d0b732e651d4657f10cf16744d3278d11186efe`;
- analysis contract v2:
  `25d0cfff0fba81979d15b5b70df842fc2e84f969574fa4cd73fc7ad2527c9318`;
- I5C extractor implementation:
  `9b44bb7236ef03c5b6c65ed5a225f8507a300ce3a929acc6242ee6f17f2525e5`;
- statistics contract v1:
  `2646f0e83f164fec7dccbe151b19e95b2efb13d223c673f6962c03d88803ca24`;
- I5D implementation:
  `7ed9e9a852252bb4228598378ce92c02879e2ca4f9161102c81117547d89a10f`.

The v3 Pilot manifest contains 2400 cases and remains byte-identical at
SHA-256
`e3f4bd9abc9236780489b13f97bdd02475d4560e2293d5101a970aedf793fe2f`.
The candidate governance update does not change that scientific identity.

## Observability and bounded smoke

The bound runtime contract is trace schema 3 with observability summary
contract v2. Activation remains:

```text
--b4-observability-summary
--b4-summary-horizon 30000
--b4-observability-contract-version 2
```

Each task must have at least 100 independently recomputed adjudicable jobs.
An adjudicable job has absolute deadline less than or equal to `H_B4`.
Deadline-miss denominators and mechanism opportunity denominators use `NA`
when zero.

The previously audited smoke recorded executable SHA-256
`944cf2505d9cdc99e93824c4d4f4a1eb290d049419f3085794f421415bd0e17e`
but did not seal the complete project-library closure. The new executable and
complete closure therefore cannot be proved byte-identical to that evidence:
`PILOT_RUNTIME_BYTE_IDENTICAL_TO_AUDITED_SMOKE = NO`.

One accepted schema3-v2 bounded validation root was run with the sealed Pilot
runtime: 2 tasksets by 9 algorithms, 18/18 executions, 18/18 committed
publications, 18/18 strict audit admission, zero retries, zero timeouts, zero
audit failures, and zero infrastructure failures. All 13 mechanism fields are
present and their subset/count invariants pass. The strict audit is
`overall_pass=true`, has SHA-256
`e6767adc6d5cee862625e45ff635663d3a2bba009454ba9040005c6c54282979`,
and remains `not_for_paper=true`. Its 18 result JSON files are byte-identical
to the latest accepted post-ST-counter validation evidence.

No Pilot, Negative Control, or Formal campaign was run while preparing this
authorization. I5C extracts counts only; I5D Pilot mode remains a neutral gate
without rankings, p-values, tables, or paper figures. Formal and Negative
Control modes continue to fail closed.
