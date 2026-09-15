// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;
import {ContinuityRegistry} from "../contracts/ContinuityRegistry.sol";
interface Vm {
    function addr(uint256) external returns (address);
    function sign(uint256,bytes32) external returns (uint8,bytes32,bytes32);
    function expectRevert(bytes4) external;
    function chainId(uint256) external;
}
contract ContinuityRegistryTest {
    Vm constant vm=Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    ContinuityRegistry registry;
    uint256[] keys;
    address[] committee;
    function setUp() public {
        // Synthetic integers for Foundry-only tests, never use these accounts on a public chain.
        keys.push(1);keys.push(2);keys.push(3);
        for(uint i;i<3;i++)for(uint j=i+1;j<3;j++)if(vm.addr(keys[j])<vm.addr(keys[i]))(keys[i],keys[j])=(keys[j],keys[i]);
        for(uint i;i<3;i++)committee.push(vm.addr(keys[i]));
        vm.chainId(97);registry=new ContinuityRegistry(committee,2);
    }
    function genesis() internal pure returns(ContinuityRegistry.Commitment memory) {
        return ContinuityRegistry.Commitment(bytes32(uint256(10)),bytes32(uint256(11)),bytes32(uint256(12)),bytes32(uint256(13)),0,0,0);
    }
    function sigs(ContinuityRegistry target,ContinuityRegistry.Commitment memory c,uint count) internal returns(bytes[] memory signatures) {
        signatures=new bytes[](count);
        for(uint i;i<count;i++){(uint8 v,bytes32 r,bytes32 s)=vm.sign(keys[i],target.signingDigest(c));signatures[i]=abi.encodePacked(r,s,v);}
    }
    function testQuorumCommitsAndAdvances() public {
        autoCommit(genesis());
        ContinuityRegistry.Commitment memory c=genesis();c.parent=c.checkpoint;c.checkpoint=bytes32(uint256(14));c.sequence=1;c.tick=8;autoCommit(c);
        (bytes32 g,bytes32 m,bytes32 h,uint64 seq,uint64 tick,bool exists)=registry.heads(c.lineage);
        require(exists&&g==c.graph&&m==c.model&&h==c.checkpoint&&seq==1&&tick==8,"head mismatch");
    }
    function autoCommit(ContinuityRegistry.Commitment memory c) internal {registry.commit(c,sigs(registry,c,2));}
    function testSingleWitnessCannotCommit() public {
        ContinuityRegistry.Commitment memory c=genesis();bytes[] memory s=sigs(registry,c,1);
        vm.expectRevert(ContinuityRegistry.InsufficientQuorum.selector);registry.commit(c,s);
    }
    function testDuplicateSignerCannotFormQuorum() public {
        ContinuityRegistry.Commitment memory c=genesis();bytes[] memory s=sigs(registry,c,2);s[1]=s[0];
        vm.expectRevert(ContinuityRegistry.SignersNotOrdered.selector);registry.commit(c,s);
    }
    function testWrongParentAndReplayRejected() public {
        ContinuityRegistry.Commitment memory c=genesis();bytes[] memory s=sigs(registry,c,2);registry.commit(c,s);
        vm.expectRevert(ContinuityRegistry.WrongParentOrSequence.selector);registry.commit(c,s);
        c.sequence=1;c.tick=8;c.parent=bytes32(uint256(99));s=sigs(registry,c,2);
        vm.expectRevert(ContinuityRegistry.WrongParentOrSequence.selector);registry.commit(c,s);
    }
    function testGraphSubstitutionRejected() public {
        autoCommit(genesis());ContinuityRegistry.Commitment memory c=genesis();c.parent=c.checkpoint;c.sequence=1;c.tick=8;c.graph=bytes32(uint256(99));bytes[] memory s=sigs(registry,c,2);
        vm.expectRevert(ContinuityRegistry.ModelChanged.selector);registry.commit(c,s);
    }
    function testCrossContractReplayRejected() public {
        ContinuityRegistry.Commitment memory c=genesis();bytes[] memory s=sigs(registry,c,2);ContinuityRegistry other=new ContinuityRegistry(committee,2);
        vm.expectRevert(ContinuityRegistry.UnknownWitness.selector);other.commit(c,s);
    }
    function testCrossChainReplayRejected() public {
        ContinuityRegistry.Commitment memory c=genesis();bytes[] memory s=sigs(registry,c,2);vm.chainId(56);
        vm.expectRevert(ContinuityRegistry.UnknownWitness.selector);registry.commit(c,s);
    }
    function testMalformedSignatureRejected() public {
        ContinuityRegistry.Commitment memory c=genesis();bytes[] memory s=sigs(registry,c,2);s[0]=hex"1234";
        vm.expectRevert(ContinuityRegistry.InvalidSignature.selector);registry.commit(c,s);
    }
    function testBadCommitteeRejected() public {
        vm.expectRevert(ContinuityRegistry.InvalidCommittee.selector);new ContinuityRegistry(committee,1);
    }
    function testFuzzNonconsecutiveSequenceRejected(uint64 seq) public {
        if(seq==0)return;ContinuityRegistry.Commitment memory c=genesis();c.sequence=seq;bytes[] memory s=sigs(registry,c,2);
        vm.expectRevert(ContinuityRegistry.WrongParentOrSequence.selector);registry.commit(c,s);
    }
}
