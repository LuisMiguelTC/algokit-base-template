from algopy import (
    Account, ARC4Contract, Asset, Global, Txn, UInt64, 
    arc4, gtxn, itxn, BoxMap
)

# Definizione della struttura dati ListingsData
# Contiene i dati relativi al proprietario, al prezzo
# e al Minimun Balance Requirement dell'asset.

class ListingsData(arc4.Struct):
    owner: Account
    price: UInt64
    mbr: UInt64

# Definizione della classe Marketplace.
class Marketplace(ARC4Contract):
    
    # Definizione delle variabili globali.
    FEE_BPS: UInt64
    BPS_DIVISOR: UInt64
    active_listings: UInt64

    # Metodo che inizializza la mappa dei dati utilizzando le Box,
    # associa l'ID dell'asset ai dettagli della vendita.
    def __init__(self) -> None:
        self.listings = BoxMap(UInt64, ListingsData, key_prefix="")

    # Metodo che crea l'applicazione e configura i parametri globali:
    # Fee del 1% e contatore listing.
    @arc4.abimethod(allow_actions=["NoOp"], create="require")
    def create_application(self) -> None:
        self.FEE_BPS = UInt64(100)          # 100 BPS = 1%.
        self.BPS_DIVISOR = UInt64(10_000)   # Divisore.
        self.active_listings = UInt64(0)    # Contatore listing.

    # Metodo che registra un nuovo asset nel marketplace, gestisce l'MBR per la Box 
    # e l'opt-in del contratto all'asset.
    @arc4.abimethod
    def list_asset(self, asset: UInt64, price: UInt64, mbr_pay: gtxn.PaymentTransaction) -> None:
        # Il prezzo dell'asset deve essere superiore di zero.
        assert price > 0, "Il prezzo deve essere maggiore di zero"
        # Gestione del costo del Box (Minimum Balance Requirement)
        assert mbr_pay.amount >= 100_000, "Pagamento MBR insufficiente"
        # Il mittente del pagamento deve essere chi sta listando l'asset
        assert mbr_pay.sender == Txn.sender
        # Il destinatario del pagamento deve essere il contratto
        assert mbr_pay.receiver == Global.current_application_address
        # L'asset non deve essere già listato, questo impedisce 
        # l'overwrite di listings esistenti
        assert not self.listings.maybe(asset)[1], "Asset già listato"
        
        # Se il contratto non ha ancora l'asset, deve fare l'opt-in.
        if not Global.current_application_address.is_opted_in(Asset(asset)):
            itxn.AssetTransfer(
                xfer_asset=asset,
                asset_receiver=Global.current_application_address,
                asset_amount=0,
                fee= 0, 
            ).submit()
        
        # I dati vengono inseriti nella Box che è associata all'ID dell'asset.
        self.listings[asset] = ListingsData(
            owner=Txn.sender,
            price=price,
            mbr=mbr_pay.amount
        )
        
        # Incremento della variabile globale contatore.
        self.active_listings += 1
    
    # Metodo che controlla che il deposito di asset, sia un deposito valido.
    @arc4.abimethod
    def deposit_asset(self, axfer: gtxn.AssetTransferTransaction) -> UInt64:
        
        # L'asset deve essere quello giusto.
        asset_id = axfer.xfer_asset.id
        # Il destinatario deve essere l'applicazione.
        assert (axfer.asset_receiver == Global.current_application_address), "Il ricevente deve essere il contratto"
        # L'asset deve essere tra quelli listati.
        assert self.listings.maybe(asset_id)[1], "Asset non listato"
        # La quantità di deposito deve essere positiva.
        assert axfer.asset_amount > 0, "La quantita deve essere maggiore di zero"
        # Solo il proprietario del listing può rifornire.
        assert axfer.sender == self.listings[asset_id].owner, "Solo il proprietario può depositare"

        # L'incremento è avvenuto grazie ad axfer. 
        # Viene restituito il bilancio totale aggiornato.
        return Asset(asset_id).balance(Global.current_application_address)

    # Metodo che permette l'acquisto di un asset specifico tra quelli listati.
    @arc4.abimethod
    def buy_asset(self, asset_id: UInt64, quantity: UInt64, buyer_pay: gtxn.PaymentTransaction) -> None:
        # Verifica che l'asset sia listato.
        assert self.listings.maybe(asset_id)[1], "Asset not listed"
        # Verifica che la quantità sia valida.
        assert quantity > 0, "La quantità deve essere maggiore di zero"
        # Verifica la quantità disponibile degli asset richiesti.
        assert  Asset(asset_id).balance(Global.current_application_address) >= quantity
        assert buyer_pay.sender == Txn.sendero
        expected_amount = self.listings[asset_id].price * quantity
        # Il pagamento deve essere esattamente l'importo atteso.
        assert buyer_pay.amount == expected_amount
        # Il destinatario del pagamento deve essere il contratto.
        assert buyer_pay.receiver == Global.current_application_address

        # Calcolo della fee usando FEE_BPS / BPS_DIVISOR.
        fee = (expected_amount * self.FEE_BPS) // self.BPS_DIVISOR
        seller_amount = expected_amount - fee
        
        # Trasferimento di ALGO al venditore.
        itxn.Payment(
            receiver=self.listings[asset_id].owner,
            amount=seller_amount,
            fee=0,  # Il contratto dirotta il pagamento al venditore
        ).submit()

        # Pagamento della commissione al creatore
        if fee > 0:
            itxn.Payment(
                receiver=Global.creator_address,
                amount=fee,
                fee=0,  # Il contratto dirotta il pagamento al creatore.
            ).submit()

        # Trasferimento dell'asset all'acquirente.
        itxn.AssetTransfer(
            xfer_asset=asset_id,
            asset_receiver=Txn.sender,
            asset_amount=quantity
        ).submit()
    # Metodo che ritira dal marketplace gli asset se presenti o non ed elimina
    # l'inserzione, restituendo il deposito cauzionale versato per il listing.
    @arc4.abimethod
    def delist_remaining_assets(self, asset_id: UInt64) -> None:
        
        # Verifica che l'asset sia listato.
        assert self.listings.maybe(asset_id)[1], "Asset not listed"
        # Solo il proprietario originale può ritirare i suoi asset.
        assert Txn.sender == self.listings[asset_id].owner
        # Non viene messo nessun vincolo sul fatto che ci siano o meno asset residui,
        # perchè il venditore potrebbe voler ritirare l'asset anche se è esaurito
        # eliminando quindi semplicemente il listing.
        contract_balance = Asset(asset_id).balance(Global.current_application_address)

        itxn.AssetTransfer(
            xfer_asset=asset_id,
            asset_receiver=self.listings[asset_id].owner,
            asset_amount=contract_balance,
            asset_close_to=self.listings[asset_id].owner, # Chiude l'opt-in dell'asset.
            fee=0,
        ).submit()

        # Restituzione dell'MBR al proprietario dell'asset.
        itxn.Payment(
            receiver=self.listings[asset_id].owner,
            amount=self.listings[asset_id].mbr,
            fee=0,  # Il contratto dirotta il pagamento al venditore.
        ).submit()

        # Rimozione del listing dalla BoxMap.
        del self.listings[asset_id]
        # Decremento del contatore dei listing attivi.
        self.active_listings -= 1

    # Metodo che termina e chiude il marketplace.
    @arc4.abimethod(allow_actions=["DeleteApplication"])
    def delete_application(self) -> None:
        # Solo il creatore può eliminare l'applicazione.
        assert Txn.sender == Global.creator_address
        # Nessun listing attivo deve essere presente.
        assert self.active_listings == 0, "Active listings must be zero"

        # Tutti i fondi residui verranno inviati al creatore automaticamente.
        itxn.Payment(
            receiver=Global.creator_address,
            close_remainder_to=Global.creator_address,
            amount=0,
            fee=0,
        ).submit()