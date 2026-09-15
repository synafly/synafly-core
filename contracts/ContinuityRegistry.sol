// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @notice Research commitment log, NOT a brain simulator or permissionless consensus.
/// @dev A fixed witness quorum attests off-chain replay. Bytes must remain available off-chain.
contract ContinuityRegistry {
    struct Commitment {
        bytes32 lineage;
        bytes32 graph;
        bytes32 model;
        bytes32 checkpoint;
        bytes32 parent;
        uint64 sequence;
        uint64 tick;
    }
    struct Head {
        bytes32 graph;
        bytes32 model;
        bytes32 checkpoint;
        uint64 sequence;
        uint64 tick;
        bool exists;
    }
    bytes32 public constant DOMAIN = keccak256("SynaFly.ContinuityRegistry.v1");
    uint256 private constant HALF_ORDER = 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;
    uint256 public immutable threshold;
    mapping(address => bool) public isWitness;
    mapping(bytes32 => Head) public heads;
    address[] public witnesses;

    error InvalidCommittee();
    error InvalidCommitment();
    error WrongParentOrSequence();
    error ModelChanged();
    error InsufficientQuorum();
    error InvalidSignature();
    error SignersNotOrdered();
    error UnknownWitness();

    event CheckpointCommitted(bytes32 indexed lineage, uint64 sequence, uint64 tick,
        bytes32 graph, bytes32 model, bytes32 parent, bytes32 checkpoint);

    constructor(address[] memory committee, uint256 required) {
        if (required < 2 || required > committee.length || committee.length > 16) revert InvalidCommittee();
        address previous;
        for (uint256 i; i < committee.length; ++i) {
            if (committee[i] <= previous) revert InvalidCommittee();
            isWitness[committee[i]] = true;
            witnesses.push(committee[i]);
            previous = committee[i];
        }
        threshold = required;
    }

    /// @notice Personal-sign this 32-byte message hash with an external wallet.
    function messageHash(Commitment memory c) public view returns (bytes32) {
        return keccak256(abi.encode(DOMAIN, block.chainid, address(this), c.lineage,
            c.graph, c.model, c.checkpoint, c.parent, c.sequence, c.tick));
    }

    function signingDigest(Commitment memory c) public view returns (bytes32) {
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", messageHash(c)));
    }

    /// @notice Anyone may relay a quorum certificate. No token, payment or admin override exists.
    function commit(Commitment calldata c, bytes[] calldata signatures) external {
        if (c.lineage == 0 || c.graph == 0 || c.model == 0 || c.checkpoint == 0) revert InvalidCommitment();
        Head storage h = heads[c.lineage];
        if (!h.exists) {
            if (c.sequence != 0 || c.tick != 0 || c.parent != 0) revert WrongParentOrSequence();
        } else {
            if (c.graph != h.graph || c.model != h.model) revert ModelChanged();
            if (c.sequence != h.sequence + 1 || c.tick <= h.tick || c.parent != h.checkpoint) revert WrongParentOrSequence();
        }
        if (signatures.length < threshold || signatures.length > witnesses.length) revert InsufficientQuorum();
        bytes32 signed = signingDigest(c);
        address previous;
        for (uint256 i; i < signatures.length; ++i) {
            bytes calldata signature = signatures[i];
            if (signature.length != 65) revert InvalidSignature();
            bytes32 r; bytes32 s; uint8 v;
            assembly {
                r := calldataload(signature.offset)
                s := calldataload(add(signature.offset, 32))
                v := byte(0, calldataload(add(signature.offset, 64)))
            }
            if ((v != 27 && v != 28) || uint256(s) > HALF_ORDER || uint256(s) == 0) revert InvalidSignature();
            address signer = ecrecover(signed, v, r, s);
            if (!isWitness[signer]) revert UnknownWitness();
            if (signer <= previous) revert SignersNotOrdered();
            previous = signer;
        }
        heads[c.lineage] = Head(c.graph, c.model, c.checkpoint, c.sequence, c.tick, true);
        emit CheckpointCommitted(c.lineage, c.sequence, c.tick, c.graph, c.model, c.parent, c.checkpoint);
    }
}
