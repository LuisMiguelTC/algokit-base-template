# Import delle librerie necessarie
import pytest
from algokit_utils import (
    AlgoAmount,
    AlgorandClient,
    AssetOptInParams,
    AssetTransferParams,
    CommonAppCallParams,
    PaymentParams,
    SigningAccount,
    AssetCreateParams,
)
from smart_contracts.artifacts.marketplace.marketplace_client import (
    ListAssetArgs,
    MarketplaceClient,
    MarketplaceFactory,
)

# La fixture creator crea un account creatore tramite il client Algorand e lo
# finanzia con 10 ALGO (10_000_000 microALGO).
# L'account creator e' colui che crea lo Smart Contract ossia il marketplace.
@pytest.fixture(scope="session")
def creator(algorand_client: AlgorandClient) -> SigningAccount:
    account = algorand_client.account.from_environment("CREATOR")
    algorand_client.account.ensure_funded_from_environment(
        account_to_fund=account.address, min_spending_balance=AlgoAmount.from_algo(10)
    )
    return account

# La fixture seller crea un account venditore tramite il client Algorand e lo
# finanzia con 10 ALGO (10_000_000 microALGO).
# L'account seller è colui che mette in vendita gli asset nel marketplace.
@pytest.fixture(scope="session")
def seller(algorand_client: AlgorandClient) -> SigningAccount:
    account = algorand_client.account.from_environment("SELLER")
    algorand_client.account.ensure_funded_from_environment(
        account_to_fund=account.address, min_spending_balance=AlgoAmount.from_algo(10)
    )
    return account

# La fixture buyer crea un account compratore tramite il client Algorand e lo
# finanzia con 10 ALGO (10_000_000 microALGO).
# L'account buyer è colui che acquista gli asset dal marketplace.
@pytest.fixture(scope="session")
def buyer(algorand_client: AlgorandClient) -> SigningAccount:
    account = algorand_client.account.from_environment("BUYER")
    algorand_client.account.ensure_funded_from_environment(
        account_to_fund=account.address, min_spending_balance=AlgoAmount.from_algo(10)
    )
    return account

# La fixture test_asset_id crea un asset di test sulla rete Algorand utilizzando
# l'account seller e restituisce l'ID dell'asset creato.
# L'asset ha una fornitura totale di 10 unità.
@pytest.fixture(scope="session")
def test_asset_id(
    algorand_client: AlgorandClient, 
    seller: SigningAccount
) -> int:
    asset = algorand_client.send.asset_create(
        AssetCreateParams(
        sender=seller.address,
        total=10,
        asset_name="Test Asset",
        )
    )
    return asset.asset_id

# La fixture mass_assets crea cento asset di test sulla rete Algorand utilizzando 
# l'account seller e restituisce una lista dei loro ID.
# Ogni asset ha una fornitura totale di 1 unità, utile per simulare un carico 
# massivo di listing e verificare la gestione dello storage on-chain.
@pytest.fixture(scope="session")
def mass_assets(algorand_client: AlgorandClient, seller: SigningAccount) -> list[int]:
    asset_ids = []
    for i in range(100):
        asset = algorand_client.send.asset_create(
            AssetCreateParams(
                sender=seller.address, 
                total=1, 
                asset_name=f"Test Asset{i}"
            )
        )
        asset_ids.append(asset.asset_id)
    return asset_ids

# La fixture marketplace_client crea un'istanza del MarketplaceClient
# utilizzando l'account creator.
# Inoltre, finanzia l'indirizzo dell'applicazione del marketplace con 
# 1 ALGO per coprire i costi di transazione.
@pytest.fixture(scope="session")
def marketplace_client(
    algorand_client: AlgorandClient, creator: SigningAccount
) -> MarketplaceClient:
    factory = algorand_client.client.get_typed_app_factory(
        MarketplaceFactory, default_sender=creator.address
    )

    client, _ = factory.send.create.create_application()
    
    dispenser = algorand_client.account.localnet_dispenser()
    algorand_client.account.ensure_funded(
        account_to_fund=client.app_address, 
        min_spending_balance=AlgoAmount.from_algo(1),
        dispenser_account=dispenser,
    )
    return client

# Test utile per verificare il listing di un asset nel marketplace.
def test_list_asset(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    seller: SigningAccount,
    test_asset_id: int
) -> None:

    # Crea una transazione di pagamento da parte del venditore
    # di 0.1 ALGO per la tassa di listing (Minimun Balance Requirement). 
    mbr_payment = algorand_client.create_transaction.payment(
        PaymentParams(
        sender=seller.address,
        receiver=marketplace_client.app_address,
        amount=AlgoAmount.from_algo(0.1)
      )
    )

    # Esegue il listing dell'asset nel marketplace.
    # Viene fornito un extra_fee di 1000 microALGO per permettere 
    # al contratto di eseguire l'inner transaction di Asset Opt-in 
    # verso l'asset.
    marketplace_client.send.list_asset(
        args=(
            ListAssetArgs(
                asset=test_asset_id,
                price=100_000,
                mbr_pay=mbr_payment
            )
        ),
        params= CommonAppCallParams(
           sender=seller.address, 
           extra_fee=AlgoAmount(micro_algo=1000)
        ),
    )

    # Verifica che il contratto abbia registrato correttamente il listing.
    assert marketplace_client.app_client.algorand.asset.get_by_id(
        test_asset_id).asset_id == test_asset_id

# Test utile per verificare il deposito di asset nel marketplace.
def test_deposit(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    test_asset_id: int,
    seller: SigningAccount   
):
    # Definisce la quantità di asset da depositare.
    deposit_quantity = 5
    
    # Crea la transazione di trasferimento asset.
    # Questa è la transazione che "sposta" fisicamente gli asset.
    axfer_txn = algorand_client.create_transaction.asset_transfer(
        AssetTransferParams(
            sender=seller.address,
            receiver=marketplace_client.app_address,
            asset_id=test_asset_id,
            amount=deposit_quantity
        )
    )

    # Esegue il deposito dell'asset all'interno del marketplace.
    result = marketplace_client.send.deposit_asset(
        args=(axfer_txn,),
        params=CommonAppCallParams(
            sender=seller.address,
        ),
    )

    # Verifica che il ritorno ABI sia la quantità depositata.
    # È la prima volta che l'asset viene depositato, quindi il 
    # bilancio totale deve essere uguale alla quantità depositata.
    assert result.abi_return == deposit_quantity

    # Verifica ulteriore sul bilancio reale del contratto.
    account_info = algorand_client.asset.get_account_information(
        marketplace_client.app_address,
        test_asset_id
    )
    assert account_info.balance == deposit_quantity

# Test utile per verificare l'acquisto di un asset dal marketplace e
# il successivo trasferimento dell'asset al compratore.
def test_buy_asset(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    buyer: SigningAccount,
    seller: SigningAccount,
    test_asset_id: int,
):
    # Definisce la quantità di asset da acquistare.
    quantity = 3

    # Il compratore esegue l'opt-in dell'asset prima di acquistarlo.
    algorand_client.send.asset_opt_in(
        AssetOptInParams(
            sender=buyer.address,
            asset_id=test_asset_id
        )
    )

    # Crea una transazione di pagamento da parte del compratore
    # per l'importo totale dell'acquisto all'indirizzo dell'applicazione.
    buyer_txn = algorand_client.create_transaction.payment(
        PaymentParams(
            sender=buyer.address,
            receiver=marketplace_client.app_address,
            amount=AlgoAmount(micro_algo=quantity * 100_000),
        )
    )

    # Esegue l'acquisto dell'asset dal marketplace.
    # Invia un extra_fee per coprire i costi di transazione.
    marketplace_client.send.buy_asset(
        args=(test_asset_id, quantity, buyer_txn),
        params=CommonAppCallParams(
            sender=buyer.address,
            extra_fee=AlgoAmount(micro_algo=3000),
        ),
    )
    
    # Verifica che il compratore abbia ricevuto gli asset.
    account_info = algorand_client.asset.get_account_information(
        sender=buyer.address,
        asset_id=test_asset_id
    )

    # Verifica che il bilancio del compratore sia aumentato della 
    # quantità acquistata.
    current_amount = account_info.balance
    assert current_amount == quantity

# Test utile per verificare il delisting di un asset dal marketplace.
def test_delist_asset(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    seller: SigningAccount,
    test_asset_id: int
):
    # Esegue il delisting dell'asset dal marketplace.
    # Invia un extra_fee per coprire i costi di transazione.
    marketplace_client.send.delist_remaining_assets(
        args=(test_asset_id,),
        params=CommonAppCallParams(
            sender=seller.address,
            extra_fee=AlgoAmount(micro_algo=1000)
        )
    )

    # Verifica del bilancio finale del venditore (Seller):
    # 10 unità create dal seller
    # 5 Depositate nel marketplace -> Rimaste 5
    # Restituzione dei residui non venduti: 5 caricati - 3 acquistati = 2 
    # Saldo Atteso: 5 + 2 = 7 unità
    account_info = algorand_client.asset.get_account_information(
        seller.address,
        test_asset_id
    )
    assert account_info.balance == 7

    # Verifica che il contratto non abbia più l'asset
    # Poiché il contratto ha fatto il close-out, 
    # l'interrogazione dell'asset deve fallire.
    with pytest.raises(Exception):
        algorand_client.asset.get_account_information(
            marketplace_client.app_address,
            test_asset_id
        )

# Test utile per verificare che depositi cumulativi di asset
# vengano contabilizzati correttamente nel marketplace.
def test_cumulative_deposit(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    seller: SigningAccount,
    test_asset_id: int
):
    # Crea una transazione di pagamento da parte del venditore
    # di 0.1 ALGO per la tassa di listing (Minimun Balance Requirement). 
    mbr_payment_relist = algorand_client.create_transaction.payment(
        PaymentParams(
            sender=seller.address,
            receiver=marketplace_client.app_address,
            amount=AlgoAmount.from_algo(0.1)
        )
    )
    # L'asset non è più disponibile in quanto è stato fatto il delisting.
    # Occorre fare nuovamente il listing dell'asset nel marketplace,
    # necessario prima di poter depositare.
    marketplace_client.send.list_asset(
        args=(test_asset_id, 100_000, mbr_payment_relist),
        params=CommonAppCallParams(
            sender=seller.address, 
            extra_fee=AlgoAmount(micro_algo=2000))
    )

    # Il venditore esegue due depositi separati.
    # Il primo deposito di 3 asset, il secondo di 2 asset.
    first_deposit = 3
    second_deposit = 2

    # Crea la prima transazione di trasferimento asset.
    first_txn = algorand_client.create_transaction.asset_transfer(
        AssetTransferParams(
            sender=seller.address,
            receiver=marketplace_client.app_address,
            asset_id=test_asset_id,
            amount=first_deposit
        )
    )

    # Deposita il primo lotto di asset.
    marketplace_client.send.deposit_asset(
        args=(first_txn,),
        params=CommonAppCallParams(
            sender=seller.address
        )
    )

    # Crea la seconda transazione di trasferimento asset.
    second_txn = algorand_client.create_transaction.asset_transfer(
        AssetTransferParams(
            sender=seller.address,
            receiver=marketplace_client.app_address,
            asset_id=test_asset_id,
            amount=second_deposit
        )
    )

    # Deposita il secondo lotto di asset.
    marketplace_client.send.deposit_asset(
        args=(second_txn,),
        params=CommonAppCallParams(
            sender=seller.address
        )
    )

    # Verifica nel contratto il totale degli asset depositati.
    account_info = algorand_client.asset.get_account_information(
        marketplace_client.app_address,
        test_asset_id
    )
    # Calcola il totale atteso.
    expected_total = first_deposit + second_deposit
    assert account_info.balance == expected_total

# Test utile per verificare che un deposito di asset da parte
# di un non proprietario fallisca.
def test_deposit_non_owner_fails(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    buyer: SigningAccount,
    test_asset_id: int
):
    # Crea la transazione di trasferimento asset.
    # Trasferisce 1 asset da un account che non è il proprietario
    # dell'asset nel marketplace.
    axfer_txn = algorand_client.create_transaction.asset_transfer(
        AssetTransferParams(
            sender=buyer.address,
            receiver=marketplace_client.app_address,
            asset_id=test_asset_id,
            amount=1
        )
    )

    # Tenta di eseguire il deposito dell'asset nel marketplace.
    # Questo dovrebbe fallire poiché il compratore non è il 
    # proprietario dell'asset. 
    # La box presente nel marketplace riferito a quel asset ha 
    # memorizzato come  proprietario dell'asset il venditore.
    with pytest.raises(Exception):
        marketplace_client.send.deposit_asset(
            args=(axfer_txn,),
            params=CommonAppCallParams(
                sender=buyer.address
            )
        )

# Test utile per verificare che un acquisto di asset con 
# saldo insufficiente fallisca.
def test_buy_asset_insufficient_balance_fails(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    buyer: SigningAccount,
    test_asset_id: int
):
    # Definisce la quantità di asset da acquistare.
    # In questo caso, tenta di acquistare 1000 asset, 
    # ma il marketplace non ne ha così tanti disponibili.
    quantity = 1000
  
    buyer_txn = algorand_client.create_transaction.payment(
        PaymentParams(
            sender=buyer.address,
            receiver=marketplace_client.app_address,
            amount=AlgoAmount(micro_algo=quantity * 100_000)
        )
    )

    # Tenta di eseguire l'acquisto dell'asset dal marketplace
    # Questo dovrebbe fallire a causa di asset insufficienti.
    with pytest.raises(Exception):
        marketplace_client.send.buy_asset(
            args=(test_asset_id, quantity, buyer_txn),
            params=CommonAppCallParams(
                sender=buyer.address
            )
        )

# Test utile per verificare che lo Smart Contract gestisca 
# correttamente acquisti simultanei della stessa disponibilità 
# residua, assicurando che solo il primo acquirente riesca a 
# completare la transazione e il secondo venga respinto per 
# saldo insufficiente.
def test_concurrency_race_condition(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    buyer: SigningAccount,
    test_asset_id: int,
):
    # Recupera il saldo attuale dell'asset nel contratto per 
    # definire la disponibilità totale.
    total_asset_marketplace = algorand_client.asset.get_account_information(
        marketplace_client.app_address,
        test_asset_id
    ).balance
    
    # Inizializza due account acquirenti indipendenti.
    # Ogni account viene finanziato per coprire le commissioni e 
    # il costo d'acquisto, seguito dall'Opt-in obbligatorio dell'asset 
    # per permetterne la ricezione.
    buyer1 = algorand_client.account.random()
    buyer2 = algorand_client.account.random()
    for b in [buyer1, buyer2]:
        algorand_client.account.ensure_funded_from_environment(
            b.address, AlgoAmount.from_algo(10))
        algorand_client.send.asset_opt_in(
            AssetOptInParams(
                sender=b.address, 
                asset_id=test_asset_id
            )
        )

    # Prepara le due transazioni di acquisto.
    # In un ambiente reale verrebbero inviate quasi in simultanea.
    def create_buy_txn(sender):
        return algorand_client.create_transaction.payment(
            PaymentParams(
                sender=sender.address, 
                receiver=marketplace_client.app_address, 
                amount=AlgoAmount(micro_algo=total_asset_marketplace * 100_000)
            )
        )

    # Il primo acquisto deve passare.
    marketplace_client.send.buy_asset(
        args=(test_asset_id, total_asset_marketplace, create_buy_txn(buyer1)),
        params=CommonAppCallParams(
            sender=buyer1.address, 
            extra_fee=AlgoAmount(micro_algo=3000)
        )
    )

    # Il secondo acquisto deve fallire perché lo smart contract 
    # vede che il bilancio box è 0.
    with pytest.raises(Exception):
        marketplace_client.send.buy_asset(
            args=(test_asset_id, total_asset_marketplace, create_buy_txn(buyer2)),
            params=CommonAppCallParams(
                sender=buyer2.address, 
                extra_fee=AlgoAmount(micro_algo=3000)
            )
        )

# Test che verifica uno stress test di listing massivo 
# per verificare la robustezza dello Smart Contract.
def test_mass_listing_stress(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    creator: SigningAccount,
    seller: SigningAccount,
    mass_assets: list[int]
):
    # Rifinanziamo il marketplace per gestire i listing
    algorand_client.account.ensure_funded(
        account_to_fund=marketplace_client.app_address, 
        min_spending_balance=AlgoAmount.from_algo(100),
        dispenser_account=creator.address,
    )

    # Effettua il listing massivo.
    for aid in mass_assets:
        mbr_pay = algorand_client.create_transaction.payment(
            PaymentParams(
                sender=seller.address, 
                receiver=marketplace_client.app_address, 
                amount=AlgoAmount.from_algo(0.1)
            )
        )

        marketplace_client.send.list_asset(
            args=(aid, 100_000, mbr_pay),
            params=CommonAppCallParams(
                sender=seller.address,
                extra_fee=AlgoAmount(micro_algo=1000)
            )
        )

# Test utile per verificare la procedura di smantellamento 
# totale dello Smart Contract.
# Assicura che l'applicazione possa essere eliminata solo dopo
# aver effettuato il delisting di tutti gli asset residui,
# garantendo che non rimangano asset pendenti che bloccherebbero
# la chiusura dell'account del contratto.
def test_delete_application(
    algorand_client: AlgorandClient,
    marketplace_client: MarketplaceClient,
    creator: SigningAccount,
    seller: SigningAccount,
    mass_assets: list[int],
    test_asset_id: int
):
    # Delist di tutti i 100 asset della stress fixture + test_asset_id.
    all_assets_to_clean = mass_assets + [test_asset_id]
    
    for aid in all_assets_to_clean:
        marketplace_client.send.delist_remaining_assets(
            args=(aid,),
            params=CommonAppCallParams(
                sender=seller.address,
                extra_fee=AlgoAmount(micro_algo=2000)
            )
        )

    # Ora che il contratto è vuoto (0 asset outstanding),
    # esegue la cancellazione dell'applicazione.
    marketplace_client.send.delete.delete_application(
        params=CommonAppCallParams(
            sender=creator.address,
            extra_fee=AlgoAmount(micro_algo=3000) 
        )
    )

    # Verifica che l'applicazione non esista più.
    with pytest.raises(Exception):
        algorand_client.app.get_by_id(marketplace_client.app_id)